# Copyright 2017-2018 Biomedical Imaging Group Rotterdam, Departments of
# Medical Informatics and Radiology, Erasmus MC, Rotterdam, The Netherlands
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import sklearn

from PREDICT.classification import crossval as cv
from PREDICT.classification import construct_classifier as cc
from PREDICT.plotting.plot_SVM import plot_multi_SVM
from PREDICT.plotting.plot_SVR import plot_single_SVR
import PREDICT.IOparser.file_io as file_io
import PREDICT.IOparser.config_io_classifier as config_io


def trainclassifier(feat_train, patientinfo_train, config,
                    output_hdf, output_json,
                    feat_test=None, patientinfo_test=None,
                    fixedsplits=None, verbose=True):
    '''
    Train a classifier using machine learning from features. By default, if no
    split in training and test is supplied, a cross validation
    will be performed.

    Parameters
    ----------
    feat_train: string, mandatory
            contains the paths to all .hdf5 feature files used.
            modalityname1=file1,file2,file3,... modalityname2=file1,...
            Thus, modalities names are always between a space and a equal
            sign, files are split by commas. We assume that the lists of
            files for each modality has the same length. Files on the
            same position on each list should belong to the same patient.

    patientinfo: string, mandatory
            Contains the path referring to a .txt file containing the
            patient label(s) and value(s) to be used for learning. See
            the Github Wiki for the format.

    config: string, mandatory
            path referring to a .ini file containing the parameters
            used for feature extraction. See the Github Wiki for the possible
            fields and their description.

    output_hdf: string, mandatory
            path refering to a .hdf5 file to which the final classifier and
            it's properties will be written to.

    output_json: string, mandatory
            path refering to a .json file to which the performance of the final
            classifier will be written to. This file is generated through one of
            the PREDICT plotting functions.

    feat_test: string, optional
            When this argument is supplied, the machine learning will not be
            trained using a cross validation, but rather using a fixed training
            and text split. This field should contain paths of the test set
            feature files, similar to the feat_train argument.

    patientinfo_test: string, optional
            When feat_test is supplied, you can supply optionally a patient label
            file through which the performance will be evaluated.

    fixedsplits: string, optional
            By default, random split cross validation is used to train and
            evaluate the machine learning methods. Optionally, you can provide
            a .xlsx file containing fixed splits to be used. See the Github Wiki
            for the format.

    verbose: boolean, default True
            print final feature values and labels to command line or not.

    '''
    # Load variables from the config file
    config = config_io.load_config(config)

    # if type(feat_train) is list:
    #     feat_train = ''.join(feat_train)

    if type(patientinfo_train) is list:
        patientinfo_train = ''.join(patientinfo_train)

    if type(config) is list:
        config = ''.join(config)

    label_type = config['Genetics']['label_names']

    # Split the feature files per modality
    feat_train_temp = list()
    modnames = list()
    for feat_mod in feat_train:
        feat_mod_temp = [str(item).strip() for item in feat_mod.split(',')]

        # The first item contains the name of the modality, followed by a = sign
        temp = [str(item).strip() for item in feat_mod_temp[0].split('=')]
        modnames.append(temp[0])
        feat_mod_temp[0] = temp[1]

        # Append the files to the main list
        feat_train_temp.append(feat_mod_temp)

    feat_train = feat_train_temp

    # Read the features and classification data
    label_data_train, image_features_train =\
        file_io.load_data(feat_train, patientinfo_train,
                          label_type, modnames)

    if feat_test is not None:
        # Split the features per modality
        feat_test_temp = [str(item).strip() for item in feat_test.split('=')]
        feat_test_temp = feat_test_temp[1::]  # First item is the first modality name
        feat_test = list()
        for feat_mod in feat_test_temp:
            feat_mod_temp = [str(item).strip() for item in feat_mod.split(',')]

            # Last item contains name of next modality if multiple, seperated by a space
            space = feat_mod_temp[-1].find(' ')
            if space != -1:
                feat_mod_temp[-1] = feat_mod_temp[-1][0:space]
            feat_test.append(feat_mod_temp)

        label_data_test, image_features_test =\
            file_io.load_data(feat_test, patientinfo_test, label_type,
                              modnames=modnames)

    # Create tempdir name from patientinfo file name
    basename = os.path.basename(patientinfo_train)
    filename, _ = os.path.splitext(basename)
    path = patientinfo_train
    for i in range(4):
        # Use temp dir: result -> sample# -> parameters - > temppath
        path = os.path.dirname(path)

    _, path = os.path.split(path)
    path = os.path.join(path, 'trainclassifier', filename)

    # Construct the required classifier
    classifier, param_grid =\
        cc.construct_classifier(config, image_features_train)

    # Append the feature groups to the parameter grid
    if config['General']['FeatureCalculator'] == 'CalcFeatures':
        param_grid['SelectGroups'] = 'True'
        for group in config['SelectFeatGroup'].keys():
            param_grid[group] = config['SelectFeatGroup'][group]

    # if config['FeatureSelection']['SelectFromModel']:
    #     param_grid['SelectFromModel'] = ['Lasso', False]

    if config['FeatureScaling']['scale_features']:
        if type(config['FeatureScaling']['scaling_method']) is not list:
            param_grid['FeatureScaling'] = [config['FeatureScaling']['scaling_method']]
        else:
            param_grid['FeatureScaling'] = config['FeatureScaling']['scaling_method']

    # Extract parameter grid settings for SearchCV from config
    param_grid['Featsel_Variance'] = config['Featsel']['Variance']
    param_grid['Imputation'] = config['Imputation']['Use']
    param_grid['ImputationMethod'] = config['Imputation']['strategy']
    param_grid['ImputationNeighbours'] = config['Imputation']['n_neighbors']
    param_grid['SelectFromModel'] = config['Featsel']['SelectFromModel']
    param_grid['UsePCA'] = config['Featsel']['UsePCA']
    param_grid['PCAType'] = config['Featsel']['PCAType']

    # For N_iter, perform k-fold crossvalidation
    if feat_test is None:
        trained_classifier = cv.crossval(config, label_data_train,
                                         image_features_train,
                                         classifier, param_grid,
                                         use_fastr=config['Classification']['fastr'],
                                         fixedsplits=fixedsplits)
    else:
        trained_classifier = cv.nocrossval(config, label_data_train,
                                           label_data_test,
                                           image_features_train,
                                           image_features_test,
                                           classifier, param_grid,
                                           config['Classification']['fastr'])

    if type(output_hdf) is list:
        output_hdf = ''.join(output_hdf)

    if not os.path.exists(os.path.dirname(output_hdf)):
        os.makedirs(os.path.dirname(output_hdf))

    trained_classifier.to_hdf(output_hdf, 'SVMdata')

    # Calculate statistics of performance
    if feat_test is None:
        if type(classifier) == sklearn.svm.SVR:
            statistics = plot_single_SVR(trained_classifier, label_data_train,
                                         label_type)
        else:
            statistics, _ = plot_multi_SVM(trained_classifier, label_data_train,
                                           label_type)
    else:
        if patientinfo_test is not None:
            if type(classifier) == sklearn.svm.SVR:
                statistics = plot_single_SVR(trained_classifier,
                                             label_data_test,
                                             label_type)
            else:
                statistics, _ = plot_multi_SVM(trained_classifier,
                                               label_data_test,
                                               label_type)
        else:
            statistics = None

    # Save output
    savedict = dict()
    savedict["Statistics"] = statistics

    if type(output_json) is list:
        output_json = ''.join(output_json)

    if not os.path.exists(os.path.dirname(output_json)):
        os.makedirs(os.path.dirname(output_json))

    with open(output_json, 'w') as fp:
        json.dump(savedict, fp, indent=4)

    print("Saved data!")
