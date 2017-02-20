import os, sys, time, glob
import numpy as np
from collections import defaultdict
from sf_miscellaneous import times, load_pickle, write_pickle, read_fasta, write_in_fa, multips
from sf_cluster_protein import diamond_run, filter_hits_single, parse_geneCluster, cleanup_clustering #mcl_run

def mcl_run(clustering_path, threads, input_prefix, mcl_inflation):
    """ """
    start = time.time()
    os.chdir(clustering_path)
    command_mcl=''.join(['mcl ',input_prefix,'_filtered_hits.abc --abc ',\
                        '-o ',input_prefix,'_cluster.output -I ',str(mcl_inflation),\
                        ' -te ',str(threads),' > ','mcl-',input_prefix,'.log 2>&1'])
    os.system(command_mcl)
    print 'run command line mcl in ',clustering_path,': \n', command_mcl
    print 'mcl runtime for ', input_prefix,': ', times(start), '\n'
    os.chdir('../../../../')

def calculate_aln_consensus(aln_file):
    """ """
    aln_dt= read_fasta(aln_file)
    alphabet = 'ACDEFGHIKLMNPQRSTVWY*-X'#alphabet = 'ACGT-N'
    if len(aln_dt) == 1:
        ## only one seq
        ## if letters not in alphabet:
        consensus_arr_seq=''.join([ ic if ic in alphabet else 'X' for ic in aln_dt.values()[0] ])
    else: 
        ## consensus of multiple seqs
        try:
            aln_array = np.array([ i for i in aln_dt.values()])
            aln_array = aln_array.view('S1').reshape((aln_array.size, -1))
            af = np.zeros((len(alphabet), aln_array.shape[1]))
            for ai, state in enumerate(alphabet):
                af[ai] += (aln_array==state).mean(axis=0)
            ## assign invalid character to the last letter in alphabet (N for nuc or X for aa )
            af[-1] = 1.0 - af[:-1].sum(axis=0)
            consensus_arr_seq=''.join([ alphabet[ic] for ic in af.argmax(axis=0) ])
        except:
            print 'errors in calculating consensus seq: ', aln_file
    return consensus_arr_seq

def build_consensus_cluster_multmode(cluster_input, subproblem_seqs_path,
    clustering_path, consensus_outputfile, input_prefix, subproblem_faa_dict, index=None):
    """ """
    subproblem_geneCluster_dt= {}
    subproblem_run_number= input_prefix.split('subproblem_')[1]
    for gid, iline in enumerate(cluster_input,index):#cluster_input
        ## use time to avoid clusterID conflict
        clusterID= "GCs%s_%07d%s"%(subproblem_run_number, gid, time.strftime('%M%S',time.gmtime()))
        gene_ids= iline.rstrip().split('\t')
        subproblem_geneCluster_dt[clusterID]= gene_ids
        #print 'debug', clusterID, gene_ids
        ## write amino-acid sequences
        faa_file= ''.join([subproblem_seqs_path,clusterID,'.faa'])
        with open(faa_file, 'wb') as cluster_aa_write:
            for gene_id in gene_ids:
                write_in_fa(cluster_aa_write, gene_id, subproblem_faa_dict[gene_id])
        ## align amino-acid sequences with mafft
        aln_file= ''.join([subproblem_seqs_path,clusterID,'.aln'])
        if len(read_fasta(faa_file))!=1:
            command_mafft= ''.join(['mafft --amino --anysymbol --quiet ',faa_file,' > ',aln_file])
            os.system(command_mafft)
        else:
            os.system('cp %s %s'%(faa_file,aln_file))
        ## calculate consensus of aligned sequences
        consensus_seq= calculate_aln_consensus(aln_file)
        ## write in consensus strain
        with open(consensus_outputfile, 'a') as consensus_output:
            write_in_fa(consensus_output, clusterID, consensus_seq)
        ## write subproblem_geneCluster_dt
        write_pickle(''.join([clustering_path,input_prefix,'_',str(index),'_dict.cpk']),\
                        subproblem_geneCluster_dt)

def build_consensus_cluster(clustering_path, threads, input_prefix):
    """ build consensus cluster """
    start = time.time()
    cluster_file= ''.join([clustering_path,input_prefix,'_cluster.output'])
    consensus_outputfile= ''.join([clustering_path,input_prefix,'_consensus','.faa'])
    subproblem_seqs_path= '%ssubproblem_cluster_seqs/'%clustering_path
    subproblem_merged_faa= ''.join([clustering_path,input_prefix,'.faa'])
    subproblem_faa_dict= read_fasta(subproblem_merged_faa)
    with open(cluster_file, 'rb') as cluster_input:
        subproblem_geneCluster_dt= defaultdict(list)
        cluster_input_lines= [iline for iline in cluster_input]
        # alternative (workable!): write to each cpk and then merge
        multips(build_consensus_cluster_multmode, threads, cluster_input_lines, 
            subproblem_seqs_path, clustering_path, consensus_outputfile, input_prefix, subproblem_faa_dict, index_needed=True)
        merged_dt={}
        for sub_dict in glob.iglob(''.join([clustering_path,input_prefix,'*_dict.cpk'])):
            merged_dt.update(load_pickle(sub_dict))
        write_pickle(''.join([clustering_path,input_prefix,'_dicts.cpk']), merged_dt)
    print 'build consensus clusters for', input_prefix,': ', times(start), '\n'

def clustering_subproblem(clustering_path, threads, subproblem_merged_faa,
        diamond_evalue, diamond_max_target_seqs, diamond_identity,
        diamond_query_cover, diamond_subject_cover,
        mcl_inflation,last_run_flag):
    """ clustering on subproblems """
    if last_run_flag==0:
        diamond_identity= diamond_query_cover= diamond_subject_cover='90'
    else:
        diamond_identity= diamond_query_cover= diamond_subject_cover='30'

    diamond_run(clustering_path, subproblem_merged_faa, threads,
                diamond_evalue, diamond_max_target_seqs, diamond_identity,
                diamond_query_cover, diamond_subject_cover)
    input_prefix= subproblem_merged_faa.split('.faa')[0]
    filter_hits_single(clustering_path, threads, input_prefix=input_prefix)
    mcl_run(clustering_path, threads, input_prefix, mcl_inflation)
    if last_run_flag==0:
        build_consensus_cluster(clustering_path, int(threads), input_prefix)

def concatenate_faa_file(clustering_path, sub_list, subproblem_merged_faa):
    """ """
    command_cat= ''.join(['cat ',' '.join(sub_list),' > ',clustering_path, subproblem_merged_faa])
    #print command_cat
    os.system(command_cat)

def integrate_clusters(clustering_path, cluster_fpath):
    """ integrate all clusters """
    ## consensus ID as key, original gene IDs as value
    consensus_to_origin_dict=defaultdict()    
    for idict in glob.iglob(clustering_path+"*_dicts.cpk"):
        consensus_to_origin_dict.update(load_pickle(idict))
    with open('%s%s'%(clustering_path,'subproblem_finalRound_cluster.output')) \
                                                    as finalRound_cluster,\
        open(cluster_fpath,'wb') as integrated_cluster:
            for iline in finalRound_cluster:
                integrated_cluster.write('%s\n'%'\t'.join([geneID 
                                    for consensusID in iline.rstrip().split('\t') \
                                    for geneID in consensus_to_origin_dict[consensusID]
                                        ]))

def clustering_divide_conquer(path, folders_dict, threads,
    diamond_evalue, diamond_max_target_seqs, diamond_identity,
    diamond_query_cover, diamond_subject_cover, mcl_inflation, subset_size=50):
    """
    Use divide and conquer algorithm to break down large all-aginst-all alignment problem
    on many strains (e.g.: >100 strains) into smaller sub-all-aginst-all-alignment on
    subsets of strains.
    All consensus cluster sequence from each sub-all-aginst-all-alignment will be used to
    finish the last run. The final cluster includes then merged sets from each run.
    """
    threads=str(threads)
    protein_path= folders_dict['protein_path']
    clustering_path= folders_dict['clustering_path']
    cluster_fpath= '%s%s'%(clustering_path,'allclusters.tsv')
    cluster_dt_cpk_fpath='%s%s'%(clustering_path,'allclusters.cpk')

    os.system('mkdir -p %ssubproblem_cluster_seqs'%clustering_path)
    faa_list= glob.glob(protein_path+"*.faa")
    #subset_size=50
    subproblems_count, leftover_count= divmod(len(faa_list),subset_size)
    all_faa_list=[]
    if subproblems_count==0:
    ## set_size < subset_size, does not need to apply divide_and_conquer
        print len(faa_list)
    else:
        for i in range(0, subproblems_count):
            sub_list= faa_list[i*subset_size : (i+1)*subset_size]
            subproblem_merged_faa= 'subproblem_%s.faa'%str(i+1)
            concatenate_faa_file(clustering_path, sub_list, subproblem_merged_faa)
            clustering_subproblem(clustering_path, threads, subproblem_merged_faa,
                                diamond_evalue, diamond_max_target_seqs,diamond_identity,
                                diamond_query_cover, diamond_subject_cover,
                                mcl_inflation, last_run_flag=0)
            #print len(sub_list)
            if i==subproblems_count-1 and leftover_count!=0: # the left-overs
                sub_list= faa_list[(i+1)*subset_size : len(faa_list)]
                subproblem_merged_faa= 'subproblem_%s.faa'%str(i+2)
                concatenate_faa_file(clustering_path, sub_list, subproblem_merged_faa)
                clustering_subproblem(clustering_path, threads, subproblem_merged_faa,
                                    diamond_evalue, diamond_max_target_seqs,diamond_identity,
                                    diamond_query_cover, diamond_subject_cover,
                                    mcl_inflation, last_run_flag=0)
                ## TODO-mightbe
                ## decide whether to distribute the leftover to each subproblem
                ## if leftover_count/subproblems_count:
        ## final run
        sub_list= glob.iglob('%s%s'%(clustering_path, '*_consensus.faa'))
        subproblem_merged_faa= 'subproblem_finalRound.faa'
        concatenate_faa_file(clustering_path, sub_list, subproblem_merged_faa)
        clustering_subproblem(clustering_path, threads, subproblem_merged_faa,
                            diamond_evalue,diamond_max_target_seqs, diamond_identity,
                            diamond_query_cover, diamond_subject_cover, mcl_inflation,
                            last_run_flag=1)
    integrate_clusters(clustering_path,cluster_fpath)
    cleanup_clustering(clustering_path)
    parse_geneCluster(cluster_fpath,cluster_dt_cpk_fpath)
