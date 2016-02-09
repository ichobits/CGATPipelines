import re
import glob
import numpy as np
import pandas as pd
import numpy as np
import itertools
from sklearn import manifold
from sklearn.metrics import euclidean_distances
from sklearn.preprocessing import scale as sklearn_scale
from sklearn.decomposition import PCA as sklearnPCA
import rpy2.robjects as ro
from rpy2.robjects import r as R
import rpy2.robjects.pandas2ri as py2ri
from CGATReport.Tracker import *
from CGATReport.Utils import PARAMS as P
import CGATPipelines.PipelineTracks as PipelineTracks

###################################################################
###################################################################
# parameterization

EXPORTDIR = P.get('readqc_exportdir', P.get('exportdir', 'export'))
DATADIR = P.get('readqc_datadir', P.get('datadir', '.'))
DATABASE = P.get('readqc_backend', P.get('sql_backend', 'sqlite:///./csvdb'))

###################################################################
# cf. pipeline_rnaseq.py
# This should be automatically gleaned from pipeline_rnaseq.py
###################################################################


TRACKS = PipelineTracks.Tracks(PipelineTracks.Sample).loadFromDirectory(
    glob.glob("%s/*.sra" % DATADIR), "(\S+).sra") +\
    PipelineTracks.Tracks(PipelineTracks.Sample).loadFromDirectory(
        glob.glob("%s/*.fastq.gz" % DATADIR), "(\S+).fastq.gz") +\
    PipelineTracks.Tracks(PipelineTracks.Sample).loadFromDirectory(
        glob.glob("%s/*.fastq.1.gz" % DATADIR), "(\S+).fastq.1.gz") +\
    PipelineTracks.Tracks(PipelineTracks.Sample).loadFromDirectory(
        glob.glob("*.csfasta.gz"), "(\S+).csfasta.gz")

###########################################################################


class RnaseqqcTracker(TrackerSQL):

    '''Define convenience tracks for plots'''

    def __init__(self, *args, **kwargs):
        TrackerSQL.__init__(self, *args, backend=DATABASE, **kwargs)


##############################################################
##############################################################
##############################################################


class SampleHeatmap(RnaseqqcTracker):
    table = "transcript_quantification"
    py2ri.activate()

    def getTracks(self, subset=None):
        return ("all")

    def getCurrentRDevice(self):

        '''return the numerical device id of the
        current device'''

        return R["dev.cur"]()[0]

    def hierarchicalClustering(self, dataframe):
        '''
        Perform hierarchical clustering on a
        dataframe of expression values

        Arguments
        ---------
        dataframe: pandas.Core.DataFrame
          a dataframe containing gene IDs, sample IDs
          and gene expression values

        Returns
        -------
        correlations: pandas.Core.DataFrame
          a dataframe of a pair-wise correlation matrix
          across samples.  Uses the Pearson correlation.
        '''

        # set sample_id to index
        pivot = dataframe.pivot(index="sample_id",
                                columns="transcript_id",
                                values="TPM")
        transpose = pivot.T
        # why do I have to resort to R????
        r_df = py2ri.py2ri_pandasdataframe(transpose)
        R.assign("p.df", r_df)
        R('''p.mat <- apply(p.df, 2, as.numeric)''')
        R('''cor.df <- cor(p.mat)''')
        r_cor = R["cor.df"]
        py_cor = py2ri.ri2py_dataframe(r_cor)
        corr_frame = py_cor

        return corr_frame

    def __call__(self, track, slice=None):
        statement = ("SELECT sample_id,transcript_id,TPM from %(table)s "
                     "WHERE transcript_id != 'Transcript';")
        df = pd.DataFrame.from_dict(self.getAll(statement))
        # insert clustering function here

        mdf = self.hierarchicalClustering(df)
        mdf.columns = set(df["sample_id"])
        mdf.index = set(df["sample_id"])

        return mdf


class sampleMDS(RnaseqqcTracker):
	# to add:
	# - ability to use rlog or variance stabalising transformatio
	# - ability to change filter threshold fo rlowly expressed transcripts
	# - JOIN with design table to get further aesthetics for plotting
	#   E.g treatment, replicate, etc

	table = "transcript_quantification"

	def __call__(self, track,  slice=None):

		# remove WHERE when table cleaned up to remove header rows
		statement = (
			"SELECT transcript_id, TPM, sample_id FROM %(table)s "
			"where transcript_id != 'Transcript'")

		# fetch data
		df = pd.DataFrame.from_dict(self.getAll(statement))

		df = df.pivot('transcript_id', 'sample_id')['TPM']

		# calculate dissimilarities
		similarities = euclidean_distances(df.transpose())

		# run MDS
		mds = manifold.MDS(n_components=2, max_iter=3000,
								eps=1e-9, dissimilarity="precomputed", n_jobs=1)
		mds = mds.fit(similarities)
		pos = pd.DataFrame(mds.embedding_)

		pos.columns = ["MD1", "MD2"]
		pos['sample'] = df.columns

		return pos  


class samplePCA(RnaseqqcTracker):
	'''
	Perform Principal component analysis on dataframe of
	expression values using sklearn PCA function. Takes expression
	dataframe, logs transforms data and scales variables to unit variance
	before performing PCA.  

	Arguments
	---------
	dataframe: pandas.Core.DataFrame
	a dataframe containing gene IDs, sample IDs
	and gene expression values

	Returns
	-------
	dataframe : pandas.Core.DataFrame
	a dataframe of first(PC1) and second (PC2) pricipal components 
	in columns across samples, which are across the rows. '''
	# to add:
	# - ability to use rlog or variance stabalising transformation instead log2
	# - ability to change filter threshold fo rlowly expressed transcripts
	# - JOIN with design table to get further aesthetics for plotting
	#   E.g treatment, replicate, etc

	table = "transcript_quantification"

	def __call__(self, track,  slice=None):

		# remove WHERE when table cleaned up to remove header rows
		statement = (
			"SELECT transcript_id, TPM, sample_id FROM %(table)s "
			"where transcript_id != 'Transcript'")

		# fetch data
		df = self.getDataFrame(statement)

		#put dataframe so row=genes, cols = samples, cells contain TPM
		pivot_df = df.pivot('transcript_id', 'sample_id')['TPM']

		#filter dataframe to get rid of genes where TPM == 0 across samples
		filtered_df = pivot_df[pivot_df.sum(axis=1) > 0]

		#add +1 to counts and log transform data. 
		logdf = np.log(filtered_df + 1)

		#Scale dataframe so variance =1 across rows
		logscaled = sklearn_scale(logdf, axis=1)

		#turn array back to df and add transcript id back to index
		logscaled_df = pd.DataFrame(logscaled)
		logscaled_df.index = list(logdf.index)


		# Now do the PCA - can change n_components
		sklearn_pca = sklearnPCA(n_components = 2)
		sklearn_pca.fit(logscaled_df)

		#these are the principle componets row 0 = PC1, 1 =PC2 etc
		PC_df = pd.DataFrame(sklearn_pca.components_)
		PC_df.index =['PC1', 'PC2']

		#This is what want for ploting bar graph 
		#y = sklearn_pca.explained_variance_ratio_

		return PC_df.T



# TS: Correlation trackers should be simplified and use tracks to
# select subsets
class CorrelationSummaryA(RnaseqqcTracker):
    table = "binned_means_correlation"
    select = ["AA", "AT", "AC", "AG"]
    select = ",".join(select)

    def __call__(self, track,  slice=None):
        statement = ("SELECT sample,%(select)s FROM %(table)s")
        # fetch data
        df = pd.DataFrame.from_dict(self.getAll(statement))
        df['sample'] = [x.replace("_quant.sf", "") for x in df['sample']]
        df = pd.melt(df, id_vars="sample")
        df2 = pd.DataFrame(map(lambda x: x.split("-"), df['sample']))
        df2.columns = ["id_"+str(x) for x in range(1, len(df2.columns)+1)]
        merged = pd.concat([df, df2], axis=1)
        # merged.index = ("all",)*len(merged.index)
        # merged.index.name = "track"
        return merged


class GradientSummaryA(CorrelationSummaryA):
    table = "binned_means_gradients"


class CorrelationSummaryT(CorrelationSummaryA):
    table = "binned_means_correlation"
    select = ["TA", "TT", "TC", "TG"]
    select = ",".join(select)


class GradientSummaryT(CorrelationSummaryT):
    table = "binned_means_gradients"


class CorrelationSummaryC(CorrelationSummaryA):
    table = "binned_means_correlation"
    select = ["CA", "CT", "CC", "CG"]
    select = ",".join(select)


class GradientSummaryC(CorrelationSummaryC):
    table = "binned_means_gradients"


class CorrelationSummaryG(CorrelationSummaryA):
    table = "binned_means_correlation"
    select = ["GA", "GT", "GC", "GG"]
    select = ",".join(select)


class GradientSummaryG(CorrelationSummaryG):
    table = "binned_means_gradients"


class CorrelationSummaryGC(CorrelationSummaryA):
    table = "binned_means_correlation"
    select = ["GC_Content", "length"]
    select = ",".join(select)


class GradientSummaryGC(CorrelationSummaryGC):
    table = "binned_means_gradients"


class BiasFactors(RnaseqqcTracker):
    table = "binned_means"

    def getTracks(self):
        d = self.get("SELECT DISTINCT factor FROM %(table)s")
        return tuple([x[0] for x in d])

    def __call__(self, track, slice=None):
        statement = "SELECT * FROM %(table)s WHERE factor = '%(track)s'"
        # fetch data
        df = self.getDataFrame(statement)

        # TS: this should be replaces with a merge with the table of
        # experiment information
        df2 = pd.DataFrame(map(lambda x: x.split("-"), df['sample']))
        df2.columns = ["id_"+str(x) for x in range(1, len(df2.columns)+1)]

        merged = pd.concat([df, df2], axis=1)
        # merged.index = ("all",)*len(merged.index)
        # merged.index.name = "track"

        return merged


class ExpressionDistribution(RnaseqqcTracker):
    table = "transcript_quantification"

    def __call__(self, track, slice=None):
        statement = """SELECT sample_id, transcript_id, RPKM
        FROM %(table)s WHERE transcript_id != 'Transcript'"""

        df = pd.DataFrame.from_dict(self.getAll(statement))
        c = 0.0000001
        df['log2rpkm'] = df['RPKM'].apply(lambda x: np.log2(c + x))

        return df


class SampleOverlapsExpress(RnaseqqcTracker):
    '''
    Tracker class to compute overlap of expression for each 
    sample on a pair-wise basis.  Returns a table of
    sample x sample overlaps, where the overlap is the
    number of common genes expressed in each pair of
    samples.
    '''

    table = "transcript_quantification"

    def __call__(self, track, slice=None):
        statement = """SELECT sample_id, transcript_id
        FROM %(table)s
        WHERE TPM >= 100;"""

        df = pd.DataFrame.from_dict(self.getAll(statement))

        overlaps = self.getOverlaps(dataframe=df)
        return overlaps

    def getOverlaps(self, dataframe):
        '''
        Pass in a dataframe of samples and
        expressed genes > threshold.
        Return an nxn dataframe of sample
        overlaps
        '''
        dataframe.index = dataframe["sample_id"]
        samples = set(dataframe.index)
        pairs = itertools.combinations_with_replacement(iterable=samples,
                                                        r=2)
        _df = pd.DataFrame(columns=samples, index=samples)
        _df.fillna(0.0, inplace=True)

        for comb in pairs:
            s1, s2 = comb
            s1_gene = set(dataframe.loc[s1]["transcript_id"])
            s2_gene = set(dataframe.loc[s2]["transcript_id"])
            gene_intersect = s1_gene.intersection(s2_gene)
            size = len(gene_intersect)
            _df.loc[s1, s2] = size
            _df.loc[s2, s1] = size

        return _df

# class ExpressionDistributionNotR(RnaseqqcTracker, SingleTableTrackerColumns):
#    table = "transcript_quantification"
#    column = "transcript_id"
#    exclude_columns = "RPKM"

#    def __call__(self, track, slice=None):
#        statement = ("SELECT sample_id, transcript_id, RPKM FROM %(table)s WHERE transcript_id != 'Transcript'")
#        df = pd.DataFrame.from_dict(self.getAll(statement))
#        c = 0.0000001
#        df['log2rpkm'] = df['RPKM'].apply(lambda x: np.log2(c + x))
#        pivot = df.pivot(index='sample_id', columns='transcript_id', values='log2rpkm')

#        return pivot

# cgatreport-test -t ExpressionDistribution -r density-plot
