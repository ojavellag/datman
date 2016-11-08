#!/usr/bin/env python
"""
Generates quality control reports on defined MRI data types. If no subject is
given, all subjects are submitted individually to the queue.

usage:
    dm-qc-report.py [options] <config>

Arguments:
    <config>           Project configuration file

Options:
    --subject SCANID   Scan ID to QC for. E.g. DTI_CMH_H001_01_01
    --rewrite          Rewrite the html of an existing qc page
    --debug            Be extra chatty

Details:
    This program QCs the data contained in <NiftiDir> and <DicomDir>, and
    outputs a myriad of metrics as well as a report in <QCDir>. All work is done
    on a per-subject basis.

    **data directories**

    The folder structure expected is that generated by xnat-export.py:

        <NiftiDir>/
           subject1/
               file1.nii.gz
               file2.nii.gz
           subject2/
               file1.nii.gz
               file2.nii.gz

        <DicomDir>/
           subject1/
               file1.dcm
               file2.dcm
           subject2/
               file1.dcm
               file2.dcm

     There should be a .dcm file for each .nii.gz. One subfolder for each
     subject will be created under the <QCDir> folder.

     **gold standards**

     To check for changes to the MRI machine's settings over time, this compares
     the headers found in <DicomDir> with the appropriate dicom file found in
     <StandardsDir>/<Tag>/filename.dcm.

     **configuration file**

     The locations of the dicom folder, nifti folder, qc folder, gold standards
     folder, log folder, and expected set of scans are read from the supplied
     configuration file with the following structure:

     paths:
       dcm: '/archive/data/SPINS/data/dcm'
       nii: '/archive/data/SPINS/data/nii'
       qc:  '/archive/data/SPINS/qc'
       std: '/archive/data/SPINS/metadata/standards'
       log: '/archive/data/SPINS/log'

     Sites:
       site1:
         XNAT_Archive: '/path/to/arc001'
         ExportInfo:
           - T1:  {Pattern: {'regex1', 'regex2'}, Count: n_expected}
           - DTI: {Pattern: {'regex1', 'regex2'}, Count: n_expected}
       site2 :
         XNAT_Archive: '/path/to/arc001'
         ExportInfo:
           - T1:  {Pattern: {'regex1', 'regex2'}, Count: n_expected}
           - DTI: {Pattern: {'regex1', 'regex2'}, Count: n_expected}
Requires:
    FSL
    QCMON
"""

import os, sys
import re
import glob
import time
import logging

import numpy as np
import pandas as pd
import yaml

import datman as dm
from datman.docopt import docopt

logging.basicConfig(level=logging.WARN, format="[%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(os.path.basename(__file__))

REWRITE = False

def slicer(fpath, pic, slicergap, picwidth):
    """
    Uses FSL's slicer function to generate a montage png from a nifti file
        fpath       -- submitted image file name
        slicergap   -- int of "gap" between slices in Montage
        picwidth    -- width (in pixels) of output image
        pic         -- fullpath to for output image
    """
    dm.utils.run("slicer {} -S {} {} {}".format(fpath,slicergap,picwidth,pic))

def sort_scans(filenames):
    """
    Takes a list of filenames, and orders them by sequence number.
    """
    sorted_series = []
    for fn in filenames:
        sorted_series.append(int(dm.scanid.parse_filename(fn)[2]))

    idx = np.argsort(sorted_series)
    sorted_names = np.asarray(filenames)[idx].tolist()

    return sorted_names

def nifti_basename(fpath):
    """
    return basename with out .nii.gz extension
    """
    basefpath = os.path.basename(fpath)
    stem = basefpath.replace('.nii.gz','')

    return(stem)

def add_image(qchtml, image, title=None):
    """
    Adds an image to the report.
    """
    if title:
        qchtml.write('<center> {} </center>'.format(title))

    relpath = os.path.relpath(image, os.path.dirname(qchtml.name))
    qchtml.write('<a href="'+ relpath + '" >')
    qchtml.write('<img src="' + relpath + '" > ')
    qchtml.write('</a><br>\n')

    return qchtml

def add_header_qc(fpath, qchtml, logdata):
    """
    Adds header diff infortmation to the report.
    """
    # get the filename of the nifti in question
    filestem = os.path.basename(fpath).replace(dm.utils.get_extension(fpath),'')

    try:
        # read the log
        with open(logdata, 'r') as f:
            f = f.readlines()
    except IOError:
        logger.info("header-diff.log not found. Generating page without it.")
        f = []

    # find lines in said log that pertain to the nifti
    lines = [re.sub('^.*?: *','',line) for line in f if filestem in line]

    if not lines:
        return

    qchtml.write('<h3> {} header differences </h3>\n<table>'.format(filestem))
    for l in lines:
        qchtml.write('<tr><td>{}</td></tr>'.format(l))
    qchtml.write('</table>\n')

# PIPELINES
def ignore(filename, qc_dir, report):
    pass

def phantom_fmri_qc(filename, outputDir):
    """
    Runs the fbirn fMRI pipeline on input phantom data if the outputs don't
    already exist.
    """
    basename = nifti_basename(filename)
    output_file = os.path.join(outputDir, '{}_stats.csv'.format(basename))
    output_prefix = os.path.join(outputDir, basename)
    if not os.path.isfile(output_file):
        dm.utils.run('qc-fbirn-fmri {} {}'.format(filename, output_prefix))

def phantom_dti_qc(filename, outputDir):
    """
    Runs the fbirn DTI pipeline on input phantom data if the outputs don't
    already exist.
    """
    dirname = os.path.dirname(filename)
    basename = nifti_basename(filename)

    output_file = os.path.join(outputDir, '{}_stats.csv'.format(basename))
    output_prefix = os.path.join(outputDir, basename)

    if not os.path.isfile(output_file):
        bvec = os.path.join(dirname, basename + '.bvec')
        bval = os.path.join(dirname, basename + '.bval')
        dm.utils.run('qc-fbirn-dti {} {} {} {} n'.format(filename, bvec, bval, output_prefix))

def phantom_anat_qc(filename, outputDir):
    """
    Runs the ADNI pipeline on input phantom data if the outputs don't already
    exist.
    """
    basename = nifti_basename(filename)
    output_file = os.path.join(outputDir, '{}_adni-contrasts.csv'.format(basename))
    if not os.path.isfile(output_file):
        dm.utils.run('qc-adni {} {}'.format(filename, output_file))

def fmri_qc(filename, qc_dir, report):
    dirname = os.path.dirname(filename)
    basename = nifti_basename(filename)

    # check scan length
    output_file = os.path.join(qc_dir, basename + '_scanlengths.csv')
    if not os.path.isfile(output_file):
        dm.utils.run('qc-scanlength {} {}'.format(filename, output_file))

    # check fmri signal
    output_prefix = os.path.join(qc_dir, basename)
    output_file = output_prefix + '_stats.csv'
    if not os.path.isfile(output_file):
        dm.utils.run('qc-fmri {} {}'.format(filename, output_prefix))

    image_raw = os.path.join(qc_dir, basename + '_raw.png')
    image_sfnr = os.path.join(qc_dir, basename + '_sfnr.png')
    image_corr = os.path.join(qc_dir, basename + '_corr.png')

    if not os.path.isfile(image_raw):
        slicer(filename, image_raw, 2, 1600)
    add_image(report, image_raw, title='BOLD montage')

    if not os.path.isfile(image_sfnr):
        slicer(os.path.join(qc_dir, basename + '_sfnr.nii.gz'), image_sfnr, 2, 1600)
    add_image(report, image_sfnr, title='SFNR map')

    if not os.path.isfile(image_corr):
        slicer(os.path.join(qc_dir, basename + '_corr.nii.gz'), image_corr, 2, 1600)
    add_image(report, image_corr, title='correlation map')

def anat_qc(filename, qc_dir, report):

    image = os.path.join(qc_dir, nifti_basename(filename) + '.png')
    if not os.path.isfile(image):
        slicer(filename, image, 5, 1600)
    add_image(report, image)

def dti_qc(filename, qc_dir, report):
    dirname = os.path.dirname(filename)
    basename = nifti_basename(filename)

    bvec = os.path.join(dirname, basename + '.bvec')
    bval = os.path.join(dirname, basename + '.bval')

    output_prefix = os.path.join(qc_dir, basename)
    output_file = output_prefix + '_stats.csv'
    if not os.path.isfile(output_file):
        dm.utils.run('qc-dti {} {} {} {}'.format(filename, bvec, bval, output_prefix))

    output_file = os.path.join(qc_dir, basename + '_spikecount.csv')
    if not os.path.isfile(output_file):
        dm.utils.run('qc-spikecount {} {} {}'.format(filename, os.path.join(qc_dir, basename + '_spikecount.csv'), bval))

    image = os.path.join(qc_dir, basename + '_b0.png')
    if not os.path.isfile(image):
        slicer(filename, image, 2, 1600)
    add_image(report, image, title='b0 montage')
    add_image(report, os.path.join(qc_dir, basename + '_directions.png'), title='bvec directions')

def run_header_qc(dicom_dir, standard_dir, log_file):
    """
    For each .dcm file found in 'dicoms', find the matching site/tag file in
    'standards', and run qc-headers (from qcmon) on these files. Any
    are written to log_file.
    """

    dicoms = glob.glob(os.path.join(dicom_dir, '*'))
    standards = glob.glob(os.path.join(standard_dir, '*'))

    if not dicoms:
        logger.debug("No dicoms found in {}".format(dicom_dir))
        return

    site = dm.scanid.parse_filename(dicoms[0])[0].site

    # build standard dict
    standardDict = {}
    for s in standards:
        if dm.scanid.parse_filename(s)[0].site == site:
            standardDict[dm.scanid.parse_filename(s)[1]] = s

    for d in dicoms:
        tag = dm.scanid.parse_filename(d)[1]
        try:
            s = standardDict[tag]
        except:
            logger.debug('No standard with tag {} found in {}'.format(tag,
                    standard_dir))
            continue

        # run header check for dicom
        dm.utils.run('qc-headers {} {} {}'.format(d, s, log_file))

    if not os.path.exists(log_file):
        subject = os.path.basename(dicom_dir)
        logger.error("header-diff.log not generated for {}. ".format(subject) +
                " Check that gold standards are present for this site.")

def qc_phantom(scan_path, subject_id, config):
    """
    scan_path :         A scan folder for a phantom (non-human participant)
    subject_id :        Subject ID assigned to this participant.
    config :            The settings obtained from project_settings.yml
    """
    handlers = {
        "T1"            : phantom_anat_qc,
        "RST"           : phantom_fmri_qc,
        "DTI60-1000"    : phantom_dti_qc,
    }

    qc_dir = dm.utils.define_folder(config['paths']['qc'])
    qc_dir = dm.utils.define_folder(os.path.join(qc_dir, subject_id))

    niftis = glob.glob(os.path.join(scan_path, '*.nii.gz'))

    for nifti in niftis:
        ident, tag, series, description = dm.scanid.parse_filename(nifti)
        if tag not in handlers:
            logger.info("No QC tag {} for scan {}. Skipping.".format(tag, nifti))
            continue
        handlers[tag](nifti, qc_dir)

def find_tech_notes(path):
    """
    Search the file tree rooted at path for the tech notes pdf
    """
    resource_folder = glob.glob(path + "*")

    if resource_folder:
        resource_folder = resource_folder[0]

    for root, dirs, files in os.walk(resource_folder):
        for fname in files:
            if ".pdf" in fname:
                return os.path.join(root, fname)
    return ""

def write_tech_notes_link(report, subject_id, resources_path):
    """
    Adds a link to the tech notes for this subject to the given QC report
    """
    tech_notes = ""
    if 'CMH' in subject_id:
        tech_notes = find_tech_notes(resources_path)

    if not tech_notes:
        report.write('<p>Tech Notes not found</p>\n')
        return

    notes_path = os.path.relpath(os.path.abspath(tech_notes),
                        os.path.dirname(report.name))
    report.write('<a href="{}">'.format(notes_path))
    report.write('Click Here to open Tech Notes')
    report.write('</a><br>')

def write_table(report, exportinfo):
    report.write('<table><tr>'
                 '<th>Tag</th>'
                 '<th>File</th>'
                 '<th>Notes</th></tr>')

    for row in range(0,len(exportinfo)):
        report.write('<tr><td>{}</td>'.format(exportinfo.loc[row,'tag'])) ## table new row
        report.write('<td><a href="#{}">{}</a></td>'.format(exportinfo.loc[row,'bookmark'],exportinfo.loc[row,'File']))
        report.write('<td><font color="#FF0000">{}</font></td></tr>'.format(exportinfo.loc[row,'Note'])) ## table new row
    report.write('</table>\n')

def write_report_header(report, subject_id):
    report.write('<HTML><TITLE>{} qc</TITLE>\n'.format(subject_id))
    report.write('<head>\n<style>\n'
                'body { font-family: futura,sans-serif;'
                '        text-align: center;}\n'
                'img {width:90%; \n'
                '   display: block\n;'
                '   margin-left: auto;\n'
                '   margin-right: auto }\n'
                'table { margin: 25px auto; \n'
                '        border-collapse: collapse;\n'
                '        text-align: left;\n'
                '        width: 90%; \n'
                '        border: 1px solid grey;\n'
                '        border-bottom: 2px solid black;} \n'
                'th {background: black;\n'
                '    color: white;\n'
                '    text-transform: uppercase;\n'
                '    padding: 10px;}\n'
                'td {border-top: thin solid;\n'
                '    border-bottom: thin solid;\n'
                '    padding: 10px;}\n'
                '</style></head>\n')

    report.write('<h1> QC report for {} <h1/>'.format(subject_id))

def find_expected_files(config, scan_path, subject):
    """
    Reads in the export info from the config file and compares it to the
    contents of the nii folder. Data written to a pandas dataframe.
    """
    site = dm.scanid.parse(subject + '_01').site # add artificial repeat number

    allpaths = []
    allfiles = []
    for filetype in ('*.nii.gz', '*.nii'):
        allpaths.extend(glob.glob(scan_path + '/*' + filetype))
    for path in allpaths:
        allfiles.append(os.path.basename(path))
    allfiles = sort_scans(allfiles)


    # build a tag count dict
    tag_counts = {}
    expected_position = {}
    for tag in config['Sites'][site]['ExportInfo'].keys():
        tag_counts[tag] = 0
        # if it exists get the expected position from the config, this will let use sort the output
        if 'Order' in config['Sites'][site]['ExportInfo'][tag].keys():
            expected_position[tag] = min([config['Sites'][site]['ExportInfo'][tag]['Order']])
        else:
            expected_position[tag] = 0

    # init output pandas data frame, counter
    exportinfo = pd.DataFrame(columns=['tag', 'File', 'bookmark', 'Note', 'Sequence'])
    idx = 0

    # tabulate found data in the order they were acquired
    for fn in allfiles:
        tag = dm.scanid.parse_filename(fn)[1]

        # only check data that is defined in the config file
        if tag in config['Sites'][site]['ExportInfo'].keys():
            expected_count = config['Sites'][site]['ExportInfo'][tag]['Count']
        else:
            continue

        tag_counts[tag] += 1
        bookmark = tag + str(tag_counts[tag])
        if tag_counts[tag] > expected_count:
            notes = 'Repeated Scan'
        else:
            notes = ''
        exportinfo.loc[idx] = [tag, fn, bookmark, notes, expected_position[tag]]
        idx += 1

    # note any missing data
    for tag in config['Sites'][site]['ExportInfo'].keys():
        expected_count = config['Sites'][site]['ExportInfo'][tag]['Count']
        if tag_counts[tag] < expected_count:
            n_missing = expected_count - tag_counts[tag]
            notes = 'missing({})'.format(expected_count - tag_counts[tag])
            exportinfo.loc[idx] = [tag, '', '', notes, expected_position[tag]]
            idx += 1
    exportinfo = exportinfo.sort('Sequence')
    return(exportinfo)

def qc_subject(scan_path, subject_id, config):
    """
    scan_path :         A scan folder for a single participant.
    subject_id :        Subject ID assigned to this participant
    config :            The settings obtained from project_settings.yml

    Returns the path to the qc_<subject_id>.html file
    """
    handlers = {   # map from tag to QC function
        "T1"            : anat_qc,
        "T2"            : anat_qc,
        "PD"            : anat_qc,
        "PDT2"          : anat_qc,
        "FLAIR"         : anat_qc,
        "FMAP"          : ignore,
        "FMAP-6.5"      : ignore,
        "FMAP-8.5"      : ignore,
        "RST"           : fmri_qc,
        "EPI"           : fmri_qc,
        "SPRL"          : fmri_qc,
        "OBS"           : fmri_qc,
        "IMI"           : fmri_qc,
        "NBK"           : fmri_qc,
        "EMP"           : fmri_qc,
        "VN-SPRL"       : fmri_qc,
        "SID"           : fmri_qc,
        "MID"           : fmri_qc,
        "DTI"           : dti_qc,
        "DTI21"         : dti_qc,
        "DTI22"         : dti_qc,
        "DTI23"         : dti_qc,
        "DTI60-29-1000" : dti_qc,
        "DTI60-20-1000" : dti_qc,
        "DTI60-1000"    : dti_qc,
        "DTI60-b1000"   : dti_qc,
        "DTI33-1000"    : dti_qc,
        "DTI33-b1000"   : dti_qc,
        "DTI33-3000"    : dti_qc,
        "DTI33-b3000"   : dti_qc,
        "DTI33-4500"    : dti_qc,
        "DTI33-b4500"   : dti_qc,
        "DTI23-1000"    : dti_qc,
        "DTI69-1000"    : dti_qc,
    }

    qc_dir = dm.utils.define_folder(config['paths']['qc'])
    qc_dir = dm.utils.define_folder(os.path.join(qc_dir, subject_id))
    report_name = os.path.join(qc_dir, 'qc_{}.html'.format(subject_id))

    resources_path = os.path.join(config['paths']['resources'], subject_id)

    if os.path.isfile(report_name):
        if not REWRITE:
            logger.debug("{} exists, skipping.".format(report_name))
            return
        os.remove(report_name)

    # header diff
    dcmSubj = os.path.join(config['paths']['dcm'], subject_id)
    headerDiff = os.path.join(qc_dir, 'header-diff.log')
    if not os.path.isfile(headerDiff):
        run_header_qc(dcmSubj, config['paths']['std'], headerDiff)

    exportinfo = find_expected_files(config, scan_path, subject_id)
    with open(report_name, 'wb') as report:
        write_report_header(report, subject_id)
        write_table(report, exportinfo)
        write_tech_notes_link(report, subject_id, resources_path)
        # write_report_body
        for idx in range(0,len(exportinfo)):
            name = exportinfo.loc[idx,'File']
            if name:
                fname = os.path.join(scan_path, name)
                logger.info("QC scan {}".format(fname))
                ident, tag, series, description = dm.scanid.parse_filename(fname)
                report.write('<h2 id="{}">{}</h2>\n'.format(exportinfo.loc[idx,'bookmark'], name))

                if tag not in handlers:
                    logger.info("No QC tag {} for scan {}. Skipping.".format(tag, fname))
                    continue

                add_header_qc(fname, report, headerDiff)

                handlers[tag](fname, qc_dir, report)
                report.write('<br>')


    return report_name

def submit_qc_jobs(commands):
    """
    Submits the given commands to the queue.
    """
    for i, cmd in enumerate(commands):
        jobname = "qc_report_{}_{}".format(time.strftime("%Y%m%d-%H%M%S"), i)
        logfile = '/tmp/{}.log'.format(jobname)
        errfile = '/tmp/{}.err'.format(jobname)

        run_cmd = 'echo {} | qsub -V -q main.q ' \
                '-o {} -e {} -N {}'.format(cmd, logfile, errfile, jobname)

        rtn, out = dm.utils.run(run_cmd)

        if rtn:
            logger.error("stdout: {}".format(out))
        else:
            logger.debug(out)

def make_qc_command(subject_id, config_file):
    command = " ".join([__file__, config_file, '--subject {}'.format(subject_id)])
    if REWRITE:
        command.append(' --rewrite')
    return command

def qc_all_scans(nii_dir, config_file):
    """
    Creates a dm-qc-report.py command for each scan and submits any
    commands for human subjects to the queue to run.
    """
    human_commands = []
    phantom_commands = []

    for path in os.listdir(nii_dir):
        subject_id = os.path.basename(path)
        command = make_qc_command(subject_id, config_file)
        if '_PHA_' in subject_id:
            phantom_commands.append(command)
        else:
            human_commands.append(command)

    if human_commands:
        submit_qc_jobs(human_commands)

    if phantom_commands:
        for cmd in phantom_commands:
            rtn, out = dm.utils.run(cmd)
            if rtn:
                logger.error("stdout: {}".format(out))

def add_report_to_checklist(qc_report, checklist_path):
    """
    Add the given report's name to the QC checklist if it is not already
    present.
    """
    if not qc_report:
        return

    # remove extension from report name, so we don't double-count .pdfs vs .html
    report_name, report_ext = os.path.splitext(qc_report)

    found_reports = []

    try:
        with open(checklist_path, 'r') as checklist:
            for checklist_entry in checklist:
                checklist_entry = checklist_entry.split(' ')[0].strip()
                checklist_entry, checklist_ext = os.path.splitext(checklist_entry)
                found_reports.append(checklist_entry)
    except IOError:
        logger.info("{} does not exist. "\
                "Attempting to create it".format(checklist_path))

    if report_name in found_reports:
        return

    with open(checklist_path, 'a') as checklist:
        checklist.write(os.path.basename(report_name + report_ext) + '\n')

def qc_single_scan(path, scanid, config):
    """
    Perform QC for a single subject or phantom. Return the report name if one
    was created.
    """
    if 'PHA' in scanid:
        logger.info("QC phantom {}".format(path))
        qc_phantom(path, scanid, config)
        return ""

    logger.info("QC {}".format(path))
    report_name = qc_subject(path, scanid, config)
    return report_name

def main():

    global REWRITE

    arguments = docopt(__doc__)

    config_path = arguments['<config>']
    scanid      = arguments['--subject']
    REWRITE     = arguments['--rewrite']
    debug       = arguments['--debug']

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    with open(config_path, 'r') as stream:
        config = yaml.load(stream)

    for k in ['dcm', 'nii', 'qc', 'std', 'meta']:
        if k not in config['paths']:
            logging.error("paths:{} not defined in {}".format(k, configfile))
            sys.exit(1)

    nii_dir = config['paths']['nii']
    qc_dir = dm.utils.define_folder(config['paths']['qc'])
    meta_dir = config['paths']['meta']
    checklist_file = os.path.join(meta_dir,'checklist.csv')

    if scanid:
        dm.utils.remove_empty_files(os.path.join(qc_dir, scanid))
        path = os.path.join(nii_dir, scanid)
        checklist_path = os.path.join(meta_dir, checklist_file)

        qc_report = qc_single_scan(path, scanid, config)
        add_report_to_checklist(qc_report, checklist_path)
        return

    qc_all_scans(nii_dir, config_path)

if __name__ == "__main__":
    main()
