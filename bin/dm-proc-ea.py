#!/usr/bin/env python
"""
This analyzes empathic accuracy behavioural data.It could be generalized
to analyze any rapid event-related design experiment fairly easily.

Usage:
    dm-proc-ea.py [options] <project> <script> <assets>

Arguments: 
    <project>           Full path to the project directory containing data/.
    <script>            Full path to an epitome-style script.
    <assets>            Full path to an assets folder containing 
                                              EA-timing.csv, EA-vid-lengths.csv.

Options:
    -v,--verbose             Verbose logging
    --debug                  Debug logging

DETAILS

    1) Preprocesses MRI data.
    2) Produces an AFNI-compatible GLM file with the event timing. 
    3) Runs the GLM analysis at the single-subject level.

    Each subject is run through this pipeline if the outputs do not already exist.

DEPENDENCIES

    + matlab
    + afni

This message is printed with the -h, --help flags.
"""

import os, sys
import glob
import copy
import tempfile
import numpy as np
import scipy.interpolate as interpolate
import nibabel as nib
import StringIO as io
import matplotlib.pyplot as plt
import datman as dm
from datman.docopt import docopt

def log_parser(log):
    """
    This takes the EA task log file generated by e-prime and converts it into a
    set of numpy-friendly arrays (with mixed numeric and text fields.)

    pic -- 'Picture' lines, which contain the participant's ratings.
    res -- 'Response' lines, which contain their responses (unclear)
    vid -- 'Video' lines, which demark the start and end of trials.
    """
    # substitute for GREP -- finds 'eventtype' field.
    # required as this file has a different number of fields per line
    logname = copy.copy(log)
    log = open(log, "r").readlines()
    pic = filter(lambda s: 'Picture' in s, log)
    #res = filter(lambda s: 'Response' in s, log)
    vid = filter(lambda s: 'Video' in s, log)

    # write out files from stringio blobs into numpy genfromtxt
    pic = np.genfromtxt(io.StringIO(''.join(pic)), delimiter='\t', 
                             dtype=[('subject', '|S64'), 
                                    ('trial', 'i32'),
                                    ('eventtype', '|S64'),
                                    ('code', '|S64'),
                                    ('time', 'i32'),
                                    ('ttime', 'i32'),
                                    ('uncertainty1', 'i32'),
                                    ('duration', 'i32'),
                                    ('uncertainty2', 'i32'),
                                    ('reqtime', 'i32'),
                                    ('reqduration', 'i32'),
                                    ('stimtype', '|S64'),
                                    ('pairindex', 'i32')])

    vid = np.genfromtxt(io.StringIO(''.join(vid)), delimiter='\t',
                             dtype=[('subject', '|S64'), 
                                    ('trial', 'i32'),
                                    ('eventtype', '|S64'),
                                    ('code', '|S64'),
                                    ('time', 'i32'),
                                    ('ttime', 'i32'),
                                    ('uncertainty1', 'i32')])

    # ensure our inputs contain a 'MRI_start' string.
    if pic[0][3] != 'MRI_start':
        print('ERROR: log {} does not contain an MRI_start entry!'.format(logname))
        raise ValueError
    else:
        # this is the start of the fMRI run, all times are relative to this.
        mri_start = pic[0][7]
        return pic, vid, mri_start

def find_blocks(vid, mri_start):
    """
    Takes the start time and a vid tuple list to find the relative
    block numbers, their start times, and their type (string).
    """
    blocks = []
    onsets = []
    for v in vid:

        # we will use this to search through the response files
        block_number = v[1]

        # this is maybe useless (e.g., 'vid_4')
        block_name = v[3]

        # all time in 10000s of a sec.
        block_start = (v[4]) 

        # generate compressed video list
        blocks.append((block_number, block_name, block_start))
        onsets.append(block_start / 10000.0)

    return blocks, onsets

def find_ratings(pic, blk_start, blk_end, blk_start_time, duration):
    """
    Takes the response and picture tuple lists and the beginning of the current
    and next videos. This will search through all of the responses [vid_start
    < x < vid_end] and grab their timestamps. For each, it will find the
    corresponding picture rating and save that as an integer. 

    All times in 10,000s of a second.

    102,103 -- person responses
    104     -- MRI responses
    """

    ratings = []
    if blk_end == None:
        # find the final response number, take that as the end of our block
        trial_list = np.linspace(blk_start, pic[-1][1], pic[-1][1]-blk_start+1)
    else:
        # just use the beginning of the next block as our end.
        trial_list = np.linspace(blk_start, blk_end-1, blk_end-blk_start)

    # refine trial list to include only the first, last, and button presses
    responses = np.array(filter(lambda s: s[1] in trial_list, pic))
    
    # if the participant dosen't respond at all, freak out.
    if len(responses) == 0:
        ratings = np.array([5])
        return ratings, 0

    button_pushes = len(responses)

    for response in responses:
        ratings.append((int(response[3][-1]), response[4]))

    t = np.linspace(blk_start_time, blk_start_time+duration-1, num=duration)
    r = np.zeros(duration)

    val = 5
    last = 0
    for rating in ratings:
        idx = np.where(t == rating[1])[0]
        r[last:idx] = val  # fill in all the values before the button push\
        val = rating[0]    # update the value to insert
        last = idx         # keep track of the last button push
    r[last:] = val         # fill in the tail end of the vector with the last recorded value

    return r, button_pushes

def find_column_data(blk_name, rating_file):
    """
    Returns the data from the column of specified file with the specified name.
    """
    # read in column names, convert to lowercase, compare with block name
    column_names = np.genfromtxt(rating_file, delimiter=',', 
                                              dtype=str)[0].tolist()
    column_names = map(lambda x: x.lower(), column_names)
    column_number = np.where(np.array(column_names) == blk_name.lower())[0]

    # read in actor ratings from the selected column, strip nans
    column_data = np.genfromtxt(rating_file, delimiter=',', 
                                              dtype=float, skip_header=2)
    
    # deal with a single value
    if len(np.shape(column_data)) == 1:
        column_data = column_data[column_number]
    # deal with a column of values
    elif len(np.shape(column_data)) == 2:
        column_data = column_data[:,column_number]
    # complain if the supplied rating_file is a dungparty
    else:
        print('*** ERROR: the file you supplied is not formatted properly!')
        raise ValueError
    # strip off NaN values
    column_data = column_data[np.isfinite(column_data)]

    return column_data

def match_lengths(a, b):
    """
    Matches the length of vector b to vector a using linear interpolation.
    """

    interp = interpolate.interp1d(np.linspace(0, len(b)-1, len(b)), b)
    b = interp(np.linspace(0, len(b)-1, len(a)))

    return b

def process_behav_data(log, assets, datadir, sub, trial_type):
    """
    This parses the behavioural log files for a given trial type (either 
    'vid' for the empathic-accuracy videos, or 'cvid' for the circles task.

    First, the logs are parsed into list of 'picture', 'response', and 'video'
    events, as they contain a different number of columns and carry different
    information. The 'video' list is then used to find the start of each block.

    Within each block, this script goes about parsing the ratings made by 
    the particpant using 'find_ratings'. The timing is extracted from the 
    'response' list, and the actual rating is extracted from the 'picture' 
    list.

    This is then compared with the hard-coded 'gold-standard' rating kept in 
    a column of the specified .csv file. The lengths of these vectors are 
    mached using linear interpolaton, and finally correlated. This correlation
    value is used as an amplitude modulator of the stimulus box-car. Another
    set of amplitude-modulated regressor of no interest is added using the
    number of button presses per run. 

    The relationship between these ratings are written out to a .pdf file for 
    visual inspection, however, the onsets, durations, and correlation values
    are only returned for the specified trial type. This should allow you to 
    easily write out a GLM timing file with the onsets, lengths, 
    correlations, and number of button-pushes split across trial types.
    """

    # make sure our trial type inputs are valid
    if trial_type not in ['vid', 'cvid']:
        print('ERROR: trial_type input {} is incorrect.'.format(trial_type))
        print('VALID: vid or cvid.')
        raise ValueError

    try:
        pic, res, vid, mri_start = log_parser(log)
    except:
        print('ERROR: Failed to parse log file: {}'.format(log))
        raise ValueError

    blocks, onsets = find_blocks(vid, mri_start)
    
    durations = []
    correlations = []
    onsets_used = []
    button_pushes = []
    # format our output plot
    width, height = plt.figaspect(1.0/len(blocks))
    fig, axs = plt.subplots(1, len(blocks), figsize=(width, height*0.8))
    #fig = plt.figure(figsize=(width, height))

    for i in np.linspace(0, len(blocks)-1, len(blocks)).astype(int).tolist():

        blk_start = blocks[i][0]
        blk_start_time = blocks[i][2]

        # block end is the beginning of the next trial
        try:
            blk_end = blocks[i+1][0]
        # unless we are on the final trial of the block, then we return None
        except:
            blk_end = None

        blk_name = blocks[i][1]

        gold_rate = find_column_data(blk_name, os.path.join(assets, 'EA-timing.csv'))
        duration = find_column_data(blk_name, os.path.join(assets, 'EA-vid-lengths.csv'))[0]
        subj_rate, n_pushes = find_ratings(pic, blk_start, blk_end, blk_start_time, duration*10000)

        # interpolate the gold standard sample to match the subject sample
        if n_pushes != 0:
            gold_rate = match_lengths(subj_rate, gold_rate)
        else:
            subj_rate = np.repeat(5, len(gold_rate))

        corr = np.corrcoef(subj_rate, gold_rate)[1][0]

        if np.isnan(corr) == True:
            corr = 0  # this happens when we get no responses

        # add our ish to a kewl plot
        axs[i].plot(gold_rate, color='black', linewidth=2)
        axs[i].plot(subj_rate, color='red', linewidth=2)
        axs[i].set_title(blk_name + ': r = ' + str(corr), size=10)
        axs[i].set_xlim((0,len(subj_rate)-1))
        axs[i].set_xlabel('TR')
        axs[i].set_xticklabels([])
        axs[i].set_ylim((0, 10))
        if i == 0:
            axs[i].set_ylabel('Rating')
        if i == len(blocks) -1:
            axs[i].legend(['Actor', 'Participant'], loc='best', fontsize=10, frameon=False)

        # skip the 'other' kind of task
        if trial_type == 'vid' and blocks[i][1][0] == 'c':
            continue
        
        elif trial_type == 'cvid' and blocks[i][1][0] == 'v':
            continue
        
        # otherwise, save the output vectors in seconds
        else:
            onsets_used.append(onsets[i] - mri_start/10000.0)
            durations.append(duration.tolist())
            
            if type(corr) == int:
                correlations.append(corr)
            else:
                correlations.append(corr.tolist())
            # button pushes per minute (duration is in seconds)
            button_pushes.append(n_pushes / (duration.tolist()[0] / 60.0))

    fig.suptitle(log, size=10)
    fig.set_tight_layout(True)
    fig.savefig('{}/ea/{}_{}.pdf'.format(datadir, sub, os.path.basename(log)[:-4]))

    return onsets_used, durations, correlations, button_pushes

def process_functional_data(sub, datadir, script):
    # copy functional data into epitome-compatible structure
    try:
        niftis = filter(lambda x: 'nii.gz' in x, os.listdir(os.path.join(datadir, 'nii', sub)))
    except:
        print('ERROR: No "nii" folder found for ' + str(sub))
        raise ValueError

    # find T1s
    if os.path.isfile(os.path.join(datadir, 'freesurfer', sub, 'mri/brain.mgz')) == False:
        print('ERROR: No Freesurfered T1s found for ' + str(sub))
        raise ValueError

    # find EA task
    try:
        EA_data = filter(lambda x: 'EMP' == dm.utils.scanid.parse_filename(x)[1], niftis)
        EA_data.sort()

        # remove truncated runs
        for d in EA_data:
            nifti = nib.load(os.path.join(datadir, 'nii', sub, d))
            if nifti.shape[-1] != 277:
                EA_data.remove(d)
        EA_data = EA_data[-3:]         # take the last three
    
    except:
        print('ERROR: No/not enough EA data found for ' + str(sub))
        raise ValueError

    if len(EA_data) != 3:
        print('ERROR: Did not find all 3 EA files for ' + str(sub))
        raise ValueError

    # check if output already exists
    if os.path.isfile('{}/ea/{}_complete'.format(datadir, sub)) == True:
        raise ValueError

    tmpdir = tempfile.mkdtemp(dir='/tmp')
    dm.utils.make_epitome_folders(os.path.join(tmpdir, 'epitome'), 3)
    epidir = os.path.join(tmpdir, 'epitome/TEMP/SUBJ')
    dir_i = os.path.join(os.environ['SUBJECTS_DIR'], sub, 'mri')
    
    # T1: freesurfer data
    os.system('mri_convert --in_type mgz --out_type nii -odt float -rt nearest --input_volume {}/brain.mgz --output_volume {}/T1/SESS01/anat_T1_fs.nii.gz'.format(dir_i, epidir))
    os.system('3daxialize -prefix {epidir}/T1/SESS01/anat_T1_brain.nii.gz -axial {epidir}/T1/SESS01/anat_T1_fs.nii.gz'.format(epidir=epidir))
    
    os.system('mri_convert --in_type mgz --out_type nii -odt float -rt nearest --input_volume {}/aparc+aseg.mgz --output_volume {}/T1/SESS01/anat_aparc_fs.nii.gz'.format(dir_i, epidir))
    os.system('3daxialize -prefix {epidir}/T1/SESS01/anat_aparc_brain.nii.gz -axial {epidir}/T1/SESS01/anat_aparc_fs.nii.gz'.format(epidir=epidir))
    
    os.system('mri_convert --in_type mgz --out_type nii -odt float -rt nearest --input_volume {}/aparc.a2009s+aseg.mgz --output_volume {}/T1/SESS01/anat_aparc2009_fs.nii.gz'.format(dir_i, epidir))
    os.system('3daxialize -prefix {epidir}/T1/SESS01/anat_aparc2009_brain.nii.gz -axial {epidir}/T1/SESS01/anat_aparc2009_fs.nii.gz'.format(epidir=epidir))

    # functional data
    os.system('cp {}/nii/{}/{} {}/FUNC/SESS01/RUN01/FUNC01.nii.gz'.format(datadir, sub, str(EA_data[0]), epidir))
    os.system('cp {}/nii/{}/{} {}/FUNC/SESS01/RUN02/FUNC02.nii.gz'.format(datadir, sub, str(EA_data[1]), epidir))
    os.system('cp {}/nii/{}/{} {}/FUNC/SESS01/RUN03/FUNC03.nii.gz'.format(datadir, sub, str(EA_data[2]), epidir))
        
    # run preprocessing pipeline
    os.system('bash {} {} 4'.format(script, os.path.join(tmpdir, 'epitome')))

    # copy outputs into data folder
    if os.path.isdir(datadir + '/ea') == False:
        os.system('mkdir ' + datadir + '/ea' )

    # functional data
    os.system('cp {}/FUNC/SESS01/func_MNI-nonlin.DATMAN.01.nii.gz {}/ea/{}_func_MNI-nonlin.EA.01.nii.gz'.format(epidir, datadir, sub))
    os.system('cp {}/FUNC/SESS01/func_MNI-nonlin.DATMAN.02.nii.gz {}/ea/{}_func_MNI-nonlin.EA.02.nii.gz'.format(epidir, datadir, sub))
    os.system('cp {}/FUNC/SESS01/func_MNI-nonlin.DATMAN.03.nii.gz {}/ea/{}_func_MNI-nonlin.EA.03.nii.gz'.format(epidir, datadir, sub))
    os.system('cp {}/FUNC/SESS01/anat_EPI_mask_MNI-nonlin.nii.gz {}/ea/{}_anat_EPI_mask_MNI.nii.gz'.format(epidir, datadir, sub))
    os.system('cp {}/FUNC/SESS01/reg_T1_to_TAL.nii.gz {}/ea/{}_reg_T1_to_MNI-lin.nii.gz'.format(epidir, datadir, sub))
    os.system('cp {}/FUNC/SESS01/reg_nlin_TAL.nii.gz {}/ea/{}_reg_nlin_MNI.nii.gz'.format(epidir, datadir, sub))
    os.system('cat {}/FUNC/SESS01/PARAMS/motion.DATMAN.01.1D > {}/ea/{}_motion.1D'.format(epidir, datadir, sub))
    os.system('cat {}/FUNC/SESS01/PARAMS/motion.DATMAN.02.1D >> {}/ea/{}_motion.1D'.format(epidir, datadir, sub))
    os.system('cat {}/FUNC/SESS01/PARAMS/motion.DATMAN.03.1D >> {}/ea/{}_motion.1D'.format(epidir, datadir, sub))
    os.system('touch {}/ea/{}_preproc-complete.log'.format(datadir, sub))
    os.system('rm -r {}'.format(tmpdir))

def generate_analysis_script(sub, datadir):
    """
    This writes the analysis script to replicate the methods in Harvey et al
    2013 Schizophrenia Bulletin. It expects timing files to exist (those are
    generated by 'process_behav_data').

    Briefly, this method uses the correlation between the empathic ratings of
    the participant and the actor from each video to generate an amplitude-
    modulated box-car model to be fit to each time-series. This model is
    convolved with an HRF, and is run alongside a standard boxcar. This allows
    us to detect regions that modulate their 'activation strength' with 
    empathic accruacy, and those that generally track the watching of
    emotionally-valenced videos (but do not parametrically modulate).

    Since each video is of a different length, each block is encoded as such
    in the stimulus-timing file (all times in seconds):

        [start_time]*[amplitude]:[block_length]
        30*5:12

    See '-stim_times_AM2' in AFNI's 3dDeconvolve 'help' for more.

    """
    # first, determine input functional files
    niftis = filter(lambda x: 'nii.gz' in x and sub + '_func' in x, 
                    os.listdir(os.path.join(datadir, 'ea')))
    niftis.sort()

    input_data = ''

    for nifti in niftis:
        input_data = input_data + datadir + '/ea/' + nifti + ' '

    # open up the master script, write common variables
    f = open('{}/ea/{}_glm_1stlevel_cmd.sh'.format(datadir, sub), 'wb')
    f.write("""#!/bin/bash

# Empathic accuracy GLM for {sub}.
3dDeconvolve \\
    -input {input_data} \\
    -mask {datadir}/ea/{sub}_anat_EPI_mask_MNI.nii.gz \\
    -ortvec {datadir}/ea/{sub}_motion.1D motion_paramaters \\
    -polort 4 \\
    -num_stimts 1 \\
    -local_times \\
    -jobs 8 \\
    -x1D {datadir}/ea/{sub}_glm_1stlevel_design.mat \\
    -stim_times_AM2 1 {datadir}/ea/{sub}_block-times_ea.1D \'dmBLOCK\' \\
    -stim_label 1 empathic_accuracy \\
    -fitts {datadir}/ea/{sub}_glm_1stlevel_explained.nii.gz \\
    -errts {datadir}/ea/{sub}_glm_1stlevel_residuals.nii.gz \\
    -bucket {datadir}/ea/{sub}_glm_1stlevel.nii.gz \\
    -cbucket {datadir}/ea/{sub}_glm_1stlevel_coeffs.nii.gz \\
    -fout \\
    -tout \\
    -xjpeg {datadir}/ea/{sub}_glm_1stlevel_matrix.jpg
""".format(input_data=input_data,datadir=datadir,sub=sub))
    f.close()

def main():
    """
    1) Runs functional data through a custom epitome script.
    2) Extracts block onsets, durations, and parametric modulators from
       behavioual log files collected at the scanner (and stored in RESOURCES).
    3) Writes out AFNI-formatted timing files as well as a GLM script per
       subject.
    4) Executes this script, producing beta-weights for each subject.
    """

    global VERBOSE 
    global DEBUG
    arguments  = docopt(__doc__)
    project    = arguments['<project>']
    script     = arguments['<script>']
    assets     = arguments['<assets>']

    datadir = os.path.join(project, 'data')

    try:
        subjects = dm.utils.get_subjects(os.path.join(datadir, 'nii'))
    except:
        print('ERROR: No "nii" folder found for {}.'.format(project))
        sys.exit()

    # preprocessing loop
    for sub in subjects:
        if dm.utils.subject_type(sub) == 'phantom':
            continue
        if os.path.isfile('{}/ea/{}_preproc-complete.log'.format(datadir, sub)) == True:
            continue
        try:
            process_functional_data(sub, datadir, script)
        except ValueError as ve:
            continue

    # analysis loop
    for sub in subjects:
        if os.path.isfile('{}/ea/{}_analysis-complete.log'.format(datadir, sub)) == True:
            continue

        # get all the log files for a subject
        try:
            resdirs = glob.glob(os.path.join(datadir, 'RESOURCES', sub + '_??'))
            resources = []
            for resdir in resdirs:
                resfiles = [os.path.join(dp, f) for 
                                      dp, dn, fn in os.walk(resdir) for f in fn]
                resources.extend(resfiles)

            logs = filter(lambda x: '.log' in x and 'UCLAEmpAcc' in x, resources)
            logs.sort()
        except:
            print('ERROR: No BEHAV data for {}.'.format(sub))
            continue

        if len(logs) == 3:
            f1 = open('{}/ea/{}_block-times_ea.1D'.format(datadir, sub), 'wb') # stim timing file
            f2 = open('{}/ea/{}_corr_push.csv'.format(datadir, sub), 'wb') # r values and num pushes / minute
            f2.write('correlation,n-pushes-per-minute\n')
        else:
            print('ERROR: Need exactly 3 log files for {}'.format(sub))
            continue

        try:
            for log in logs:
                on, dur, corr, push = process_behav_data(log, assets, datadir, sub, 'vid')
                # write each stimulus time:
                #         [start_time]*[amplitude],[buttonpushes]:[block_length]
                #         30*5,0.002:12
                for i in range(len(on)):
                    f1.write('{o:.2f}*{r:.2f},{p}:{d:.2f} '.format(o=on[i], r=corr[i], p=push[i], d=dur[i]))
                    f2.write('{r:.2f},{p}\n'.format(r=corr[i], p=push[i]))
                f1.write('\n') # add newline at the end of each run (up to 3 runs.)
        except:
            print('ERROR: Failed to parse logs. Skipping analysis for {}.'.format(sub))
            pass

        finally:
            f1.close()
            f2.close()

            generate_analysis_script(sub, datadir)
            os.system('bash {}/ea/{}_glm_1stlevel_cmd.sh'.format(datadir, sub))
            os.system('touch {}/ea/{}_analysis-complete.log'.format(datadir, sub))

if __name__ == "__main__":
    main()
