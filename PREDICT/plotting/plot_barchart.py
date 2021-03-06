try:
    import matplotlib.pyplot as plt
except ImportError:
    print("[PREDICT Warning] Cannot use plot_ROC function, as _tkinter is not installed")

from matplotlib2tikz import save as tikz_save
import numpy as np
import pandas as pd
from collections import Counter
import argparse


def plot_barchart(prediction, estimators=10, label_type=None, output_tex=None,
                  output_png=None):
    '''
    Make a barchart of the top X hyperparameters settings of the ranked
    estimators in all cross validation iterations.

    Parameters
    ----------
    prediction: filepath, mandatory
        Path pointing to the .hdf5 file which was is the output of the
        trainclassifier function.

    estimators: integer, default 10
        Number of hyperparameter settings/estimators used in each cross
        validation. The settings are ranked, so when supplying e.g. 10,
        the best 10 settings in each cross validation setting will be used.

    label_type: string, default None
        The name of the label predicted by the estimator. If None,
        the first label from the prediction file will be used.

    output_tex: filepath, optional
        If given, the barchart will be written to this tex file.

    output_png: filepath, optional
        If given, the barchart will be written to this png file.

    Returns
    ----------
    fig: matplotlib figure
        The figure in which the barchart is plotted.

    '''
    # Load input prediction
    prediction = pd.read_hdf(prediction)

    # Determine for which label we extract the estimator
    keys = prediction.keys()
    if label_type is None:
        label_type = keys[0]

    prediction = prediction[label_type]

    # Extract the parameter settings:
    parameters = dict()
    for n_crossval, est in enumerate(prediction.classifiers):
        for n_setting in range(0, estimators):
            # Extract parameter settings of nth estimator
            parameters_all = est.cv_results_['params_all'][n_setting]

            # Stack settings in parameters dictionary
            for k in parameters_all.keys():
                if k not in parameters.keys():
                    parameters[k] = list()
                parameters[k].append(parameters_all[k])

    # Count for every parameter how many times a setting occurs
    counts = count_parameters(parameters)

    # Normalize the values
    normalization_factor = len(prediction.classifiers) * estimators

    # Make the barplot
    fig = plot_bars(counts, normalization_factor)

    # Save the output
    if output_tex is not None:
        print('Saving barchart to {}.').format(output_tex)
        tikz_save(output_tex)

    if output_png is not None:
        print('Saving barchart to {}.').format(output_png)
        fig.savefig(output_png, bbox_inches='tight', pad_inches=0, dpi=50)


def plot_bars(params, normalization_factor=None, figwidth=20, fontsize=20):

    # Fixing random state for reproducibility
    np.random.seed(19680801)

    # Count how often feature groups are used
    groups_temp = ['histogram', 'shape', 'orientation', 'semantic', 'patient', 'log', 'vessel', 'phase', 'coliage']
    ntimes_groups = list()
    groups = list()
    for key in groups_temp:
        key += '_features'
        if key in params.keys():
            # only append if the parameter is actually used
            if 'True' in params[key].keys():
                ntimes_groups.append(params[key]['True'])
                groups.append(key)

    # Count how often feature variance tresholding was used

    # For the texture features, we have more options than simply True and False
    texture_temp = ['True', 'LBP', 'GLCM', 'GLRLM', 'GLSZM', 'Gabor', 'NGTDM']
    texture = list()
    ntimes_texture = list()
    for key in texture_temp:
        if key in params['texture_features'].keys():
            texture.append(key)
            ntimes_texture.append(params['texture_features'][key])

    # BUG: We did not put a all in the keys but a True, so replace
    texture[texture.index('True')] = 'All'

    # # Normalize the values in order to not make figure to large
    if normalization_factor is None:
        normalization_factor = max(ntimes_groups + ntimes_texture)
    normalization_factor = float(normalization_factor)  # Needed for percentages
    ntimes_groups = [x / normalization_factor for x in ntimes_groups]
    ntimes_texture = [x / normalization_factor for x in ntimes_texture]

    # Create the figure for the barchart
    plt.rcdefaults()
    fig, ax = plt.subplots()
    fig.set_figwidth(figwidth)
    fig.set_figheight(figwidth/2)
    ax.set_xlim(0, 1)

    # Determine positions of all the labels
    y_pos = np.arange(len(groups))
    y_postick = np.arange(len(groups) + 1)

    # Normal features
    colors = ['steelblue', 'lightskyblue']
    ax.barh(y_pos, ntimes_groups, align='center',
            color=colors[0], ecolor='black')
    ax.set_yticks(y_postick)
    ax.set_yticklabels(groups + ['Texture'])
    ax.tick_params(axis='both', labelsize=fontsize)
    ax.invert_yaxis()  # labels read top-to-bottom
    ax.set_xlabel('Percentage', fontsize=fontsize)

    # Texture features
    left = 0
    y_pos = np.max(y_pos) + 1

    j = 0
    for i in np.arange(len(texture)):
        color = colors[j]
        if j == 0:
            j = 1
        else:
            j = 0
        ax.barh(y_pos, ntimes_texture[i], align='center',
                color=color, ecolor='black', left=left)
        ax.text(left + ntimes_texture[i]/2, y_pos,
                texture[i], ha='center', va='center', fontsize=fontsize - 2)
        left += ntimes_texture[i]

    return fig


def count_parameters(parameters):
    # Count for every parameter how many times a setting occurs
    output = dict()
    for setting, values in parameters.iteritems():
        output[setting] = dict()
        c = Counter(values)
        for k, v in zip(c.keys(), c.values()):
            output[setting][k] = v

    return output


def paracheck(parameters):
    # NOTE: Deprecated
    output = dict()
    # print parameters

    f = parameters['semantic_features']
    total = float(len(f))
    count_semantic = sum([i == 'True' for i in f])
    ratio_semantic = count_semantic/total
    print("Semantic: " + str(ratio_semantic))
    output['semantic_features'] = ratio_semantic

    f = parameters['patient_features']
    count_patient = sum([i == 'True' for i in f])
    ratio_patient = count_patient/total
    print("patient: " + str(ratio_patient))
    output['patient_features'] = ratio_patient

    f = parameters['orientation_features']
    count_orientation = sum([i == 'True' for i in f])
    ratio_orientation = count_orientation/total
    print("orientation: " + str(ratio_orientation))
    output['orientation_features'] = ratio_orientation

    f = parameters['histogram_features']
    count_histogram = sum([i == 'True' for i in f])
    ratio_histogram = count_histogram/total
    print("histogram: " + str(ratio_histogram))
    output['histogram_features'] = ratio_histogram

    f = parameters['shape_features']
    count_shape = sum([i == 'True' for i in f])
    ratio_shape = count_shape/total
    print("shape: " + str(ratio_shape))
    output['shape_features'] = ratio_shape

    if 'coliage_features' in parameters.keys():
        f = parameters['coliage_features']
        count_coliage = sum([i == 'True' for i in f])
        ratio_coliage = count_coliage/total
        print("coliage: " + str(ratio_coliage))
        output['coliage_features'] = ratio_coliage

    if 'phase_features' in parameters.keys():
        f = parameters['phase_features']
        count_phase = sum([i == 'True' for i in f])
        ratio_phase = count_phase/total
        print("phase: " + str(ratio_phase))
        output['phase_features'] = ratio_phase

    if 'vessel_features' in parameters.keys():
        f = parameters['vessel_features']
        count_vessel = sum([i == 'True' for i in f])
        ratio_vessel = count_vessel/total
        print("vessel: " + str(ratio_vessel))
        output['vessel_features'] = ratio_vessel

    if 'log_features' in parameters.keys():
        f = parameters['log_features']
        count_log = sum([i == 'True' for i in f])
        ratio_log = count_log/total
        print("log: " + str(ratio_log))
        output['log_features'] = ratio_log

    f = parameters['texture_features']
    count_texture_all = sum([i == 'True' for i in f])
    ratio_texture_all = count_texture_all/total
    print("texture_all: " + str(ratio_texture_all))
    output['texture_all_features'] = ratio_texture_all

    count_texture_no = sum([i == 'False' for i in f])
    ratio_texture_no = count_texture_no/total
    print("texture_no: " + str(ratio_texture_no))
    output['texture_no_features'] = ratio_texture_no

    count_texture_Gabor = sum([i == 'Gabor' for i in f])
    ratio_texture_Gabor = count_texture_Gabor/total
    print("texture_Gabor: " + str(ratio_texture_Gabor))
    output['texture_Gabor_features'] = ratio_texture_Gabor

    count_texture_LBP = sum([i == 'LBP' for i in f])
    ratio_texture_LBP = count_texture_LBP/total
    print("texture_LBP: " + str(ratio_texture_LBP))
    output['texture_LBP_features'] = ratio_texture_LBP

    count_texture_GLCM = sum([i == 'GLCM' for i in f])
    ratio_texture_GLCM = count_texture_GLCM/total
    print("texture_GLCM: " + str(ratio_texture_GLCM))
    output['texture_GLCM_features'] = ratio_texture_GLCM

    count_texture_GLRLM = sum([i == 'GLRLM' for i in f])
    ratio_texture_GLRLM = count_texture_GLRLM/total
    print("texture_GLRLM: " + str(ratio_texture_GLRLM))
    output['texture_GLRLM_features'] = ratio_texture_GLRLM

    count_texture_GLSZM = sum([i == 'GLSZM' for i in f])
    ratio_texture_GLSZM = count_texture_GLSZM/total
    print("texture_GLSZM: " + str(ratio_texture_GLSZM))
    output['texture_GLSZM_features'] = ratio_texture_GLSZM

    count_texture_NGTDM = sum([i == 'NGTDM' for i in f])
    ratio_texture_NGTDM = count_texture_NGTDM/total
    print("texture_NGTDM: " + str(ratio_texture_NGTDM))
    output['texture_NGTDM_features'] = ratio_texture_NGTDM

    if 'degree' in parameters.keys():
        f = parameters['degree']
        print("Polynomial Degree: " + str(np.mean(f)))
        output['polynomial_degree'] = np.mean(f)

    return output


def main():
    parser = argparse.ArgumentParser(description='Plot a Barchart.')
    parser.add_argument('-prediction', '--prediction', metavar='prediction',
                        nargs='+', dest='prediction', type=str, required=True,
                        help='Prediction file (HDF)')
    parser.add_argument('-estimators', '--estimators', metavar='estimator',
                        nargs='+', dest='estimators', type=str, required=False,
                        help='Number of estimators to evaluate in each cross validation.')
    parser.add_argument('-label_type', '--label_type', metavar='label_type',
                        nargs='+', dest='label_type', type=str, required=False,
                        help='Key of the label which was predicted.')
    parser.add_argument('-output_tex', '--output_tex', metavar='output_tex',
                        nargs='+', dest='output_tex', type=str, required=True,
                        help='Output file path (.tex)')
    parser.add_argument('-output_png', '--output_png', metavar='output_png',
                        nargs='+', dest='output_png', type=str, required=True,
                        help='Output file path (.png)')
    args = parser.parse_args()

    # Convert the inputs to the correct format
    if type(args.prediction) is list:
        args.prediction = ''.join(args.prediction)

    if type(args.output) is list:
        args.output = ''.join(args.output)

    if type(args.estimators) is list:
        args.estimators = int(args.estimators[0])

    if type(args.label_type) is list:
        args.label_type = ''.join(args.label_type)

    plot_barchart(prediction=args.prediction,
                  estimators=args.estimators,
                  label_type=args.label_type,
                  output_tex=args.output_tex,
                  output_png=args.output_png)


if __name__ == '__main__':
    main()
