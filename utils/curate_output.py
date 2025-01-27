import flywheel
import json
import pandas as pd
from datetime import datetime
import re
import os
import shutil

#  Module to identify the correct template use for the subject VBM analysis based on age at scan
#  Need to get subject identifiers from inside running container in order to find the correct template from the SDK


def get_age(session, dicom_header):
    
    # -------------------  Get the subject age & matching template  -------------------  #
    # uploaded demographics the most reliable, after that try to get from dicom header
    # get the T2w axi dicom acquisition from the session
    # Should contain the DOB in the dicom header
    # Some projects may have DOB removed, but may have age at scan in the subject container

    age_source = []
    age = None

    # Check if age is in custom session info
    if 'age_months' in session.info:
        age = session.info['age_months']
        age_source = 'custom_info'
        print(age, age_source)
    else:

        # Check for PatientAge in the DICOM header
        if 'PatientAge' in dicom_header.info:
            print("No custom demographic age uploaded in session info! Trying PatientAge from dicom...")
            age_raw = dicom_header.info['PatientAge']

            # Parse age and convert to months
            unit = age_raw[-1].upper()  # Extract the unit (D = Days, W = Weeks, M = Months, Y = Years)
            numeric_age = int(re.sub('\D', '', age_raw))  # Remove non-numeric characters

            if unit == 'D':  # Days to months
                age = numeric_age // 30
            elif unit == 'W':  # Weeks to months
                age = numeric_age // 4
            elif unit == 'M':  # Already in months
                age = numeric_age
            elif unit == 'Y':  # Years to months
                age = numeric_age * 12
            else:
                print("Unknown unit for PatientAge. Setting age to NA.")
                age = 'NA'

            age_source = 'dicom_age'
            print(age, age_source)

        # If PatientAge is unavailable or invalid, fallback to PatientBirthDate and SeriesDate
        if age is None or age == 0:
            if 'PatientBirthDate' in dicom_header.info and 'SeriesDate' in dicom_header.info:
                print("Trying DOB from dicom...")
                print("WARNING: This may be inaccurate if false DOB was entered at time of scanning!")

                try:
                    dob = dicom_header.info['PatientBirthDate']
                    series_date = dicom_header.info['SeriesDate']

                    # Convert dates and calculate the difference in days
                    dob_dt = datetime.strptime(dob, '%Y%m%d')
                    series_date_dt = datetime.strptime(series_date, '%Y%m%d')
                    age_days = (series_date_dt - dob_dt).days

                    # Convert days to months
                    age = age_days // 30
                    age_source = 'dicom_DOB'
                    print(age, age_source)
                except Exception as e:
                    print(f"Error parsing dates from dicom: {e}")
                    age = 'NA'
                    age_source = 'NA'
            else:
                print("No valid birthdate or series date found in dicom header. Setting age to NA.")
                age = 'NA'
                age_source = 'NA'
    return age, age_source


def housekeeping(context):

    data = []
    # Read config.json file
    p = open('/flywheel/v0/config.json')
    config = json.loads(p.read())

    # Read API key in config file
    api_key = (config['inputs']['api-key']['key'])
    fw = flywheel.Client(api_key=api_key)
    
    # Get the input file id
    input_container = context.client.get_analysis(context.destination["id"])

    # Get the subject id from the session id
    # & extract the subject container
    subject_id = input_container.parents['subject']
    subject_container = context.client.get(subject_id)
    subject = subject_container.reload()
    print("subject label: ", subject.label)
    subject_label = subject.label

    # Get the session id from the input file id
    # & extract the session container
    session_id = input_container.parents['session']
    session_container = context.client.get(session_id)
    session = session_container.reload()
    session_label = session.label
    print("session label: ", session.label)
    

    # -------------------  Get Acquisition label -------------------  #

    # Specify the directory you want to list files from
    directory_path = '/flywheel/v0/input/input'
    # List all files in the specified directory
    for filename in os.listdir(directory_path):
        if os.path.isfile(os.path.join(directory_path, filename)):
            filename_without_extension = filename.split('.')[0]
            no_white_spaces = filename_without_extension.replace(" ", "")
            cleaned_string = re.sub(r'[^a-zA-Z0-9-]', '_', no_white_spaces)
            cleaned_string = cleaned_string.rstrip('_') # remove trailing underscore

    print("cleaned_string: ", cleaned_string)

    # -------------------  Get the subject age & matching template  -------------------  #

    # get the T2w axi dicom acquisition from the session
    # Should contain the DOB in the dicom header
    # Some projects may have DOB removed, but may have age at scan in the subject container
    
    PatientSex = 'NA'
    for acq in session_container.acquisitions.iter():
        # print(acq.label)
        acq = acq.reload()
        if 'T2' in acq.label and 'AXI' in acq.label and 'Segmentation' not in acq.label and 'Align' not in acq.label: 
            for file_obj in acq.files: # get the files in the acquisition
                # Screen file object information & download the desired file
                if file_obj['type'] == 'dicom':
                    
                    dicom_header = fw._fw.get_acquisition_file_info(acq.id, file_obj.name)
                    try:
                        PatientSex = dicom_header.info["PatientSex"]
                    except:
                        PatientSex = "NA"
                        continue
                    print("PatientSex: ", PatientSex)

    age, age_source = get_age(session, dicom_header)
    
    # assign values to lists. 
    data = [{'subject': subject_label, 'session': session_label, 'age': age, 'age_source': age_source, 'sex': PatientSex, 'acquisition': cleaned_string }]  
    # Creates DataFrame.  
    demo = pd.DataFrame(data)


    # -------------------  Concatenate the data  -------------------  #

    # Start with cortical thickness data
    filePath = '/flywheel/v0/work/aparc_lh.csv'
    lh_thickness = pd.read_csv(filePath, sep='\t', engine='python')

    filePath = '/flywheel/v0/work/aparc_rh.csv'
    rh_thickness = pd.read_csv(filePath, sep='\t', engine='python')

    # smush the data together
    frames = [demo, lh_thickness, rh_thickness]
    df = pd.concat(frames, axis=1)
    out_name = f"{cleaned_string}_thickness.csv"
    outdir = ('/flywheel/v0/output/' + out_name)
    df.to_csv(outdir)

    lh_area_filePath = '/flywheel/v0/work/aparc_area_lh.csv'
    rh_area_filePath = '/flywheel/v0/work/aparc_area_rh.csv'

    lh_area = pd.read_csv(lh_area_filePath, sep='\t', engine='python')
    rh_area = pd.read_csv(rh_area_filePath, sep='\t', engine='python')

    # smush the data together
    frames = [demo, lh_area, rh_area]
    df = pd.concat(frames, axis=1)
    out_name = f"{cleaned_string}_area.csv"
    outdir = ('/flywheel/v0/output/' + out_name)
    df.to_csv(outdir)

    # volume data
    filePath = '/flywheel/v0/work/synthseg.vol.csv'
    with open(filePath) as csv_file:
        vol_data = pd.read_csv(csv_file, index_col=None, header=0) 
        vol_data = vol_data.drop('subject', axis=1)
    
    # smush the data together
    frames = [demo, vol_data]
    df = pd.concat(frames, axis=1)
    out_name = f"{cleaned_string}_volume.csv"
    outdir = ('/flywheel/v0/output/' + out_name)
    df.to_csv(outdir)

    # SynthSeg QC data
    filePath = '/flywheel/v0/work/synthseg.qc.csv'
    with open(filePath) as csv_file:
        qc_data = pd.read_csv(csv_file, index_col=None, header=0) 
        qc_data = qc_data.drop('subject', axis=1)

    # smush the data together
    frames = [demo, qc_data]
    df = pd.concat(frames, axis=1)
    out_name = f"{cleaned_string}_qc.csv"
    outdir = ('/flywheel/v0/output/' + out_name)
    df.to_csv(outdir)
    

    # Segmentation output
    synthSR_path = '/flywheel/v0/work/synthSR.nii.gz'
    aseg_path = '/flywheel/v0/work/aparc+aseg.nii.gz'

    # New file name with label
    SR_output = f"/flywheel/v0/output/{cleaned_string}_synthSR.nii.gz"
    aseg_output = f"/flywheel/v0/output/{cleaned_string}_aparc+aseg.nii.gz"

    shutil.copy(synthSR_path, SR_output)
    shutil.copy(aseg_path, aseg_output)

    # # -------------------  Generate QC image  -------------------  #


    # Run the render script to generate the QC image 

    # # Construct the command to run your bash script with variables as arguments
    # qc_command = f"/flywheel/v0/utils/render.sh '{subject_label}' '{session_label}' '{cleaned_string}' '{infant}'"

    # # Execute the bash script
    # subprocess.run(qc_command, shell=True)

    print("Demographics: ", subject_label, session_label, age, PatientSex)
