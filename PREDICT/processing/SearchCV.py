#!/usr/bin/env python

# Copyright 2011-2017 Biomedical Imaging Group Rotterdam, Departments of
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


from sklearn.base import BaseEstimator, is_classifier, clone
from sklearn.base import MetaEstimatorMixin
from sklearn.exceptions import NotFittedError
from sklearn.utils.metaestimators import if_delegate_has_method
from sklearn.utils.validation import indexable, check_is_fitted
from sklearn.metrics.scorer import check_scoring
from sklearn.model_selection._split import check_cv
from scipy.stats import rankdata
from sklearn.externals import six
from sklearn.utils.fixes import MaskedArray

from sklearn.model_selection._search import _CVScoreTuple, ParameterSampler
from sklearn.model_selection._search import ParameterGrid, _check_param_grid

from abc import ABCMeta, abstractmethod
from collections import Sized, defaultdict
import numpy as np
from functools import partial
import warnings

import os
import random
import string
import fastr
from joblib import Parallel, delayed
from PREDICT.processing.fitandscore import fit_and_score, replacenan
import PREDICT.addexceptions as PREDICTexceptions
import pandas as pd
import json
import glob
from itertools import islice
import shutil
from sklearn.metrics import f1_score, roc_auc_score, mean_squared_error
from sklearn.metrics import accuracy_score


def rms_score(truth, prediction):
    ''' Root-mean-square-error metric'''
    return np.sqrt(mean_squared_error(truth, prediction))


def sar_score(truth, prediction):
    ''' SAR metric from Caruana et al. 2004'''

    ROC = roc_auc_score(truth, prediction)
    # Convert score to binaries first
    for num in range(0, len(prediction)):
        if prediction[num] >= 0.5:
            prediction[num] = 1
        else:
            prediction[num] = 0

    ACC = accuracy_score(truth, prediction)
    RMS = rms_score(truth, prediction)
    SAR = (ACC + ROC + (1 - RMS))/3
    return SAR


def chunksdict(data, SIZE):
    '''Split a dictionary in equal parts of certain slice'''
    it = iter(data)
    for i in xrange(0, len(data), SIZE):
        yield {k: data[k] for k in islice(it, SIZE)}


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]


class Ensemble(six.with_metaclass(ABCMeta, BaseEstimator,
                                  MetaEstimatorMixin)):
    """Ensemble of BaseSearchCV Estimators."""
    # @abstractmethod
    def __init__(self, estimators):
        self.estimators = estimators
        self.n_estimators = len(estimators)

    def predict(self, X):
        """Call predict on the estimator with the best found parameters.

        Only available if ``refit=True`` and the underlying estimator supports
        ``predict``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self.estimators[0]._check_is_fitted('predict')

        outcome = np.zeros((self.n_estimators, len(X)))
        for num, est in enumerate(self.estimators):
            outcome[num, :] = est.predict(X)

        outcome = np.squeeze(np.mean(outcome, axis=0))

        # Binarize
        outcome[outcome >= 0.5] = 1
        outcome[outcome < 0.5] = 0
        return outcome

    def predict_proba(self, X):
        """Call predict_proba on the estimator with the best found parameters.

        Only available if ``refit=True`` and the underlying estimator supports
        ``predict_proba``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self.estimators[0]._check_is_fitted('predict_proba')

        # For probabilities, we get both a class0 and a class1 score
        outcome = np.zeros((len(X), 2))
        outcome_class1 = np.zeros((self.n_estimators, len(X)))
        outcome_class2 = np.zeros((self.n_estimators, len(X)))
        for num, est in enumerate(self.estimators):
            est.best_estimator_.kernel = str(est.best_estimator_.kernel)
            outcome_class1[num, :] = est.predict_proba(X)[:, 0]
            outcome_class2[num, :] = est.predict_proba(X)[:, 1]

        outcome[:, 0] = np.squeeze(np.mean(outcome_class1, axis=0))
        outcome[:, 1] = np.squeeze(np.mean(outcome_class2, axis=0))
        return outcome

    def predict_log_proba(self, X):
        """Call predict_log_proba on the estimator with the best found parameters.

        Only available if ``refit=True`` and the underlying estimator supports
        ``predict_log_proba``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self.estimators[0]._check_is_fitted('predict_log_proba')

        outcome = np.zeros((self.n_estimators, len(X)))
        for num, est in enumerate(self.estimators):
            outcome[num, :] = est.predict_log_proba(X)

        outcome = np.squeeze(np.mean(outcome, axis=0))
        return outcome

    def decision_function(self, X):
        """Call decision_function on the estimator with the best found parameters.

        Only available if ``refit=True`` and the underlying estimator supports
        ``decision_function``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self.estimators[0]._check_is_fitted('decision_function')

        outcome = np.zeros((self.n_estimators, len(X)))
        for num, est in enumerate(self.estimators):
            outcome[num, :] = est.decision_function(X)

        outcome = np.squeeze(np.mean(outcome, axis=0))
        return outcome

    def transform(self, X):
        """Call transform on the estimator with the best found parameters.

        Only available if the underlying estimator supports ``transform`` and
        ``refit=True``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self.estimators[0]._check_is_fitted('transform')

        outcome = np.zeros((self.n_estimators, len(X)))
        for num, est in enumerate(self.estimators):
            outcome[num, :] = est.transform(X)

        outcome = np.squeeze(np.mean(outcome, axis=0))
        return outcome

    def inverse_transform(self, Xt):
        """Call inverse_transform on the estimator with the best found params.

        Only available if the underlying estimator implements
        ``inverse_transform`` and ``refit=True``.

        Parameters
        -----------
        Xt : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self.estimators[0]._check_is_fitted('inverse_transform')

        outcome = np.zeros((self.n_estimators, len(Xt)))
        for num, est in enumerate(self.estimators):
            outcome[num, :] = est.transform(Xt)

        outcome = np.squeeze(np.mean(outcome, axis=0))
        return outcome


class BaseSearchCV(six.with_metaclass(ABCMeta, BaseEstimator,
                                      MetaEstimatorMixin)):
    """Base class for hyper parameter search with cross-validation."""
    @abstractmethod
    def __init__(self, estimator, param_distributions={}, n_iter=10, scoring=None,
                 fit_params=None, n_jobs=1, iid=True,
                 refit=True, cv=None, verbose=0, pre_dispatch='2*n_jobs',
                 random_state=None, error_score='raise', return_train_score=True,
                 n_jobspercore=100, maxlen=100, fastr_plugin=None):

        # Added for fastr and joblib executions
        self.param_distributions = param_distributions
        self.n_iter = n_iter
        self.n_jobspercore = n_jobspercore
        self.random_state = random_state
        self.ensemble = list()
        self.fastr_plugin = fastr_plugin

        # Below are the defaults from sklearn
        self.scoring = scoring
        self.estimator = estimator
        self.n_jobs = n_jobs
        self.fit_params = fit_params if fit_params is not None else {}
        self.iid = iid
        self.refit = refit
        self.cv = cv
        self.verbose = verbose
        self.pre_dispatch = pre_dispatch
        self.error_score = error_score
        self.return_train_score = return_train_score
        self.maxlen = maxlen


    @property
    def _estimator_type(self):
        return self.estimator._estimator_type

    def score(self, X, y=None):
        """Returns the score on the given data, if the estimator has been refit.

        This uses the score defined by ``scoring`` where provided, and the
        ``best_estimator_.score`` method otherwise.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]
            Input data, where n_samples is the number of samples and
            n_features is the number of features.

        y : array-like, shape = [n_samples] or [n_samples, n_output], optional
            Target relative to X for classification or regression;
            None for unsupervised learning.

        Returns
        -------
        score : float
        """
        if self.scorer_ is None:
            raise ValueError("No score function explicitly defined, "
                             "and the estimator doesn't provide one %s"
                             % self.best_estimator_)

        X = self.preprocess(X)

        return self.scorer_(self.best_estimator_, X, y)

    def _check_is_fitted(self, method_name):
        if not self.refit:
            raise NotFittedError(('This GridSearchCV instance was initialized '
                                  'with refit=False. %s is '
                                  'available only after refitting on the best '
                                  'parameters. ') % method_name)
        else:
            check_is_fitted(self, 'best_estimator_')

    @if_delegate_has_method(delegate=('best_estimator_', 'estimator'))
    def predict(self, X):
        """Call predict on the estimator with the best found parameters.

        Only available if ``refit=True`` and the underlying estimator supports
        ``predict``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self._check_is_fitted('predict')

        if self.ensemble:
            return self.ensemble.predict(X)
        else:
            X = self.preprocess(X)
            return self.best_estimator_.predict(X)

    @if_delegate_has_method(delegate=('best_estimator_', 'estimator'))
    def predict_proba(self, X):
        """Call predict_proba on the estimator with the best found parameters.

        Only available if ``refit=True`` and the underlying estimator supports
        ``predict_proba``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self._check_is_fitted('predict_proba')

        if self.ensemble:
            return self.ensemble.predict_proba(X)
        else:
            X = self.preprocess(X)
            return self.best_estimator_.predict_proba(X)

    @if_delegate_has_method(delegate=('best_estimator_', 'estimator'))
    def predict_log_proba(self, X):
        """Call predict_log_proba on the estimator with the best found parameters.

        Only available if ``refit=True`` and the underlying estimator supports
        ``predict_log_proba``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self._check_is_fitted('predict_log_proba')

        if self.ensemble:
            return self.ensemble.predict_log_proba(X)
        else:
            X = self.preprocess(X)
            return self.best_estimator_.predict_log_proba(X)

    @if_delegate_has_method(delegate=('best_estimator_', 'estimator'))
    def decision_function(self, X):
        """Call decision_function on the estimator with the best found parameters.

        Only available if ``refit=True`` and the underlying estimator supports
        ``decision_function``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self._check_is_fitted('decision_function')

        if self.ensemble:
            return self.ensemble.decision_function(X)
        else:
            X = self.preprocess(X)
            return self.best_estimator_.decision_function(X)

    @if_delegate_has_method(delegate=('best_estimator_', 'estimator'))
    def transform(self, X):
        """Call transform on the estimator with the best found parameters.

        Only available if the underlying estimator supports ``transform`` and
        ``refit=True``.

        Parameters
        -----------
        X : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self._check_is_fitted('transform')

        if self.ensemble:
            return self.ensemble.transform(X)
        else:
            X = self.preprocess(X)
            return self.best_estimator_.transform(X)

    @if_delegate_has_method(delegate=('best_estimator_', 'estimator'))
    def inverse_transform(self, Xt):
        """Call inverse_transform on the estimator with the best found params.

        Only available if the underlying estimator implements
        ``inverse_transform`` and ``refit=True``.

        Parameters
        -----------
        Xt : indexable, length n_samples
            Must fulfill the input assumptions of the
            underlying estimator.

        """
        self._check_is_fitted('inverse_transform')

        if self.ensemble:
            return self.ensemble.transform(Xt)
        else:
            Xt = self.preprocess(Xt)
            return self.best_estimator_.transform(Xt)

    def preprocess(self, X):
        '''Apply the available preprocssing methods to the features'''
        if self.best_groupsel is not None:
            X = self.best_groupsel.transform(X)
        if self.best_imputer is not None:
            X = self.best_imputer.transform(X)

        # Replace NaNs if they are still left at this stage, see also fit_and_score
        X = replacenan(X, self.verbose)

        if self.best_modelsel is not None:
            X = self.best_modelsel.transform(X)
        if self.best_varsel is not None:
            X = self.best_varsel.transform(X)
        if self.best_statisticalsel is not None:
            X = self.best_statisticalsel.transform(X)
        if self.best_scaler is not None:
            X = self.best_scaler.transform(X)
        if self.best_pca is not None:
            X = self.best_pca.transform(X)

        return X

    @property
    def best_params_(self):
        check_is_fitted(self, 'cv_results_')
        return self.cv_results_['params_all'][self.best_index_]

    @property
    def best_score_(self):
        check_is_fitted(self, 'cv_results_')
        return self.cv_results_['mean_test_score'][self.best_index_]

    @property
    def grid_scores_(self):
        warnings.warn(
            "The grid_scores_ attribute was deprecated in version 0.18"
            " in favor of the more elaborate cv_results_ attribute."
            " The grid_scores_ attribute will not be available from 0.20",
            DeprecationWarning)

        check_is_fitted(self, 'cv_results_')
        grid_scores = list()

        for i, (params, mean, std) in enumerate(zip(
                self.cv_results_['params'],
                self.cv_results_['mean_test_score'],
                self.cv_results_['std_test_score'])):
            scores = np.array(list(self.cv_results_['split%d_test_score'
                                                    % s][i]
                                   for s in range(self.n_splits_)),
                              dtype=np.float64)
            grid_scores.append(_CVScoreTuple(params, mean, scores))

        return grid_scores

    def process_fit(self, n_splits, parameters_est, parameters_all,
                    fitted_objects,
                    feature_labels, test_sample_counts, test_scores,
                    train_scores, fit_time, score_time, cv_iter,
                    base_estimator, X, y):

        """
        Process the outcomes of a SearchCV fit and find the best settings
        over all cross validations from all hyperparameters tested

        fitted_objects: contains items such as  GroupSel,
                        Imputers, SelectModel, VarSel, scalers, PCAs,
                        StatisticalSel. Moet een dictionary zijn!

        """
        # We take only one result per split, default by sklearn
        candidate_params_est = list(parameters_est[::n_splits])
        candidate_params_all = list(parameters_all[::n_splits])
        feature_labels = list(feature_labels[::n_splits])
        n_candidates = len(candidate_params_est)
        fitted_objects = {k: list(v[::n_splits]) for k, v in fitted_objects.iteritems()}

        # Computed the (weighted) mean and std for test scores alone
        # NOTE test_sample counts (weights) remain the same for all candidates
        test_sample_counts = np.array(test_sample_counts[:n_splits],
                                      dtype=np.int)

        # Store some of the resulting scores
        results = dict()

        def _store(key_name, array, weights=None, splits=False, rank=False):
            """A small helper to store the scores/times to the cv_results_"""
            array = np.array(array, dtype=np.float64).reshape(n_candidates,
                                                              n_splits)
            if splits:
                for split_i in range(n_splits):
                    results["split%d_%s"
                            % (split_i, key_name)] = array[:, split_i]

            array_means = np.average(array, axis=1, weights=weights)
            results['mean_%s' % key_name] = array_means
            # Weighted std is not directly available in numpy
            array_stds = np.sqrt(np.average((array -
                                             array_means[:, np.newaxis]) ** 2,
                                            axis=1, weights=weights))
            results['std_%s' % key_name] = array_stds

            if rank:
                results["rank_%s" % key_name] = np.asarray(
                    rankdata(-array_means, method='min'), dtype=np.int32)

        _store('test_score', test_scores, splits=True, rank=True,
               weights=test_sample_counts if self.iid else None)
        if self.return_train_score:
            _store('train_score', train_scores, splits=True)
        _store('fit_time', fit_time)
        _store('score_time', score_time)

        # Rank the indices of scores from all parameter settings
        ranked_test_scores = results["rank_test_score"]
        indices = range(0, len(ranked_test_scores))
        sortedindices = [x for _, x in sorted(zip(ranked_test_scores, indices))]

        # In order to reduce the memory used, we will only save at
        # a maximum of results
        maxlen = min(self.maxlen, n_candidates)
        bestindices = sortedindices[0:maxlen]

        candidate_params_est = np.asarray(candidate_params_est)[bestindices].tolist()
        candidate_params_all = np.asarray(candidate_params_all)[bestindices].tolist()
        fitted_objects = {k: np.asarray(v)[bestindices].tolist() for k, v in fitted_objects.iteritems()}

        # Feature labels cannot be indiced, as it is a list of sequences and
        # cannot be converted to a numpy aray
        feature_labels_temp = list()
        for num, f in enumerate(feature_labels):
            if num in bestindices:
                feature_labels_temp.append(f)
        feature_labels = feature_labels_temp
        for k in results.keys():
            results[k] = results[k][bestindices]
        n_candidates = len(candidate_params_est)

        # Store the atributes of the best performing estimator
        best_index = np.flatnonzero(results["rank_test_score"] == 1)[0]
        best_parameters_est = candidate_params_est[best_index]
        best_featlab = feature_labels[best_index]
        best_fitted_objects = {'best_' + k: v[best_index] for k, v in fitted_objects.iteritems()}

        # Use one MaskedArray and mask all the places where the param is not
        # applicable for that candidate. Use defaultdict as each candidate may
        # not contain all the params
        param_results = defaultdict(partial(MaskedArray,
                                            np.empty(n_candidates,),
                                            mask=True,
                                            dtype=object))
        for cand_i, params in enumerate(candidate_params_all):
            for name, value in params.items():
                # An all masked empty array gets created for the key
                # `"param_%s" % name` at the first occurence of `name`.
                # Setting the value at an index also unmasks that index
                param_results["param_%s" % name][cand_i] = value

        # Store a list of param dicts at the key 'params'
        results['params'] = candidate_params_est
        results['params_all'] = candidate_params_all

        for k, v in best_fitted_objects.iteritems():
            setattr(self, k, v)

        self.cv_results_ = results
        self.best_index_ = best_index
        self.best_featlab = best_featlab
        self.n_splits_ = n_splits
        self.cv_iter = cv_iter

        if self.refit:
            # fit the best estimator using the entire dataset
            # clone first to work around broken estimators
            best_estimator = clone(base_estimator).set_params(
                **best_parameters_est)

            # Select only the feature values, not the labels
            X = [x[0] for x in X]
            X = self.preprocess(X)

            if y is not None:
                best_estimator.fit(X, y, **self.fit_params)
            else:
                best_estimator.fit(X, **self.fit_params)
            self.best_estimator_ = best_estimator
        return self

    def refit_and_score(self, X, y, parameters_all, parameters_est,
                        train, test, verbose=None):
        """Refit the base estimator and attributes such as GroupSel

        Parameters
        ----------
        X: array, mandatory
                Array containingfor each object (rows) the feature values
                (1st Column) and the associated feature label (2nd Column).

        y: list(?), mandatory
                List containing the labels of the objects.

        parameters_all: dictionary, mandatory
                Contains the settings used for the all preprocessing functions
                and the fitting. TODO: Create a default object and show the
                fields.

        parameters_est: dictionary, mandatory
                Contains the settings used for the base estimator

        train: list, mandatory
                Indices of the objects to be used as training set.

        test: list, mandatory
                Indices of the objects to be used as testing set.


        """

        if verbose is None:
            verbose = self.verbose

        # Clone the base estimator
        base_estimator = clone(self.estimator)
        self.scorer_ = check_scoring(self.estimator, scoring=self.scoring)

        # Refit all preprocessing functions
        out = fit_and_score(clone(base_estimator), X, y, self.scorer_,
                            train, test, parameters_all,
                            fit_params=self.fit_params,
                            return_train_score=self.return_train_score,
                            return_n_test_samples=True,
                            return_times=True, return_parameters=True,
                            error_score=self.error_score,
                            verbose=verbose)

        # Associate best options with new fits
        (save_data, GroupSel, VarSel, SelectModel, feature_labels, scalers, Imputers, PCAs, StatisticalSel) = out
        self.best_groupsel = GroupSel
        self.best_scaler = scalers
        self.best_varsel = VarSel
        self.best_modelsel = SelectModel
        self.best_imputer = Imputers
        self.best_pca = PCAs
        self.best_featlab = feature_labels
        self.best_statisticalsel = StatisticalSel

        # Fit the estimator using the preprocessed features
        X = [x[0] for x in X]
        X = self.preprocess(X)

        best_estimator = clone(base_estimator).set_params(
            **parameters_est)
        if y is not None:
            best_estimator.fit(X, y, **self.fit_params)
        else:
            best_estimator.fit(X, **self.fit_params)
        self.best_estimator_ = best_estimator

        return self

    def create_ensemble(self, X_train, Y_train, verbose=None, initialize=True,
                        scoring=None, method='Top50'):
        # NOTE: Function is still WIP, do not actually use this.
        '''

        Create an (optimal) ensemble of a combination of hyperparameter settings
        and the associated groupsels, PCAs, estimators etc.

        Based on Caruana et al. 2004, but a little different:

        1. Recreate the training/validation splits for a n-fold cross validation.
        2. For each fold:
            a. Start with an empty ensemble
            b. Create starting ensemble by adding N individually best performing
               models on the validation set. N is tuned on the validation set.
            c. Add model that improves ensemble performance on validation set the most, with replacement.
            d. Repeat (c) untill performance does not increase

        The performance metric is the same as for the original hyperparameter
        search, i.e. probably the F1-score for classification and r2-score
        for regression. However, we recommend using the SAR score, as this is
        more universal.

        Method: top50 or Caruana

        '''

        # Define a function for scoring the performance of a classifier
        def compute_performance(scoring, Y_valid_truth, Y_valid_score):
            if scoring == 'f1_weighted':
                # Convert score to binaries first
                for num in range(0, len(Y_valid_score)):
                    if Y_valid_score[num] >= 0.5:
                        Y_valid_score[num] = 1
                    else:
                        Y_valid_score[num] = 0

                perf = f1_score(Y_valid_truth, Y_valid_score, average='weighted')
            elif scoring == 'auc':
                perf = roc_auc_score(Y_valid_truth, Y_valid_score)
            elif scoring == 'sar':
                perf = sar_score(Y_valid_truth, Y_valid_score)
            else:
                raise KeyError('[PREDICT Warning] No valid score method given in ensembling: ' + str(scoring))

            return perf

        if verbose is None:
            verbose = self.verbose

        if scoring is None:
            scoring = self.scoring

        # Get settings for best 100 estimators
        parameters_est = self.cv_results_['params']
        parameters_all = self.cv_results_['params_all']
        n_classifiers = len(parameters_est)
        n_iter = len(self.cv_iter)

        # Create a new base object for the ensemble components
        if type(self) == RandomizedSearchCVfastr:
            base_estimator = RandomizedSearchCVfastr(self.estimator)
        elif type(self) == RandomizedSearchCVJoblib:
            base_estimator = RandomizedSearchCVJoblib(self.estimator)

        if type(method) is int:
            # Simply take the top50 best hyperparameters
            if verbose:
                print('Creating ensemble using top {} individual classifiers.').format(str(method))
            ensemble = range(0, method)
        elif method == 'Caruana':
            # Use the method from Caruana
            if verbose:
                print('Creating ensemble with Caruana method.')

            # BUG: kernel parameter is sometimes saved in unicode
            for i in range(0, len(parameters_est)):
                kernel = str(parameters_est[i][u'kernel'])
                del parameters_est[i][u'kernel']
                del parameters_all[i][u'kernel']
                parameters_est[i]['kernel'] = kernel
                parameters_all[i]['kernel'] = kernel

            # In order to speed up the process, we precompute all scores of the possible
            # classifiers in all cross validation estimatons

            # Create the training and validation set scores
            if verbose:
                print('Precomputing scores on training and validation set.')
            Y_valid_score = list()
            Y_valid_truth = list()
            performances = np.zeros((n_iter, n_classifiers))
            for it, (train, valid) in enumerate(self.cv_iter):
                if verbose:
                    print(' - iteration {} / {}.').format(str(it + 1), str(n_iter))
                Y_valid_score_it = np.zeros((n_classifiers, len(valid)))

                # Loop over the 100 best estimators
                for num, (p_est, p_all) in enumerate(zip(parameters_est, parameters_all)):
                    # NOTE: Explicitly exclude validation set, elso refit and score
                    # somehow still seems to use it.
                    X_train_temp = [X_train[i] for i in train]
                    Y_train_temp = [Y_train[i] for i in train]
                    train_temp = range(0, len(train))

                    # Refit a SearchCV object with the provided parameters
                    base_estimator.refit_and_score(X_train_temp, Y_train_temp, p_all,
                                                   p_est, train_temp, train_temp,
                                                   verbose=False)

                    # Predict and save scores
                    X_train_values = [x[0] for x in X_train] # Throw away labels
                    X_train_values_valid = [X_train_values[i] for i in valid]
                    Y_valid_score_temp = base_estimator.predict_proba(X_train_values_valid)

                    # Only take the probabilities for the second class
                    Y_valid_score_temp = Y_valid_score_temp[:, 1]

                    # Append to array for all classifiers on this validation set
                    Y_valid_score_it[num, :] = Y_valid_score_temp

                    if num == 0:
                        # Also store the validation ground truths
                        Y_valid_truth.append(Y_train[valid])

                    performances[it, num] = compute_performance(scoring,
                                                                Y_train[valid],
                                                                Y_valid_score_temp)

                Y_valid_score.append(Y_valid_score_it)

            # Sorted Ensemble Initialization -------------------------------------
            # Go on adding to the ensemble untill we find the optimal performance
            # Initialize variables

            # Note: doing this in a greedy way doesnt work. We compute the
            # performances for the ensembles of lengt [1, n_classifiers] and
            # select the optimum
            best_performance = 0
            new_performance = 0.001
            iteration = 0
            ensemble = list()
            y_score = [None]*n_iter
            best_index = 0
            single_estimator_performance = new_performance

            if initialize:
                # Rank the models based on scoring on the validation set
                performances = np.mean(performances, axis=0)
                sortedindices = np.argsort(performances)[::-1]
                performances_n_class = list()

                if verbose:
                    print("\n")
                    print('Sorted Ensemble Initialization.')
                # while new_performance > best_performance:
                for dummy in range(0, n_classifiers):
                    # Score is better, so expand ensemble and replace new best score
                    best_performance = new_performance

                    if iteration > 1:
                        # Stack scores: not needed for first iteration
                        ensemble.append(best_index)
                        # N_models += 1
                        for num in range(0, n_iter):
                            y_score[num] = np.vstack((y_score[num], Y_valid_score[num][ensemble[-1], :]))

                    elif iteration == 1:
                        # Create y_score object for second iteration
                        single_estimator_performance = new_performance
                        ensemble.append(best_index)
                        # N_models += 1
                        for num in range(0, n_iter):
                            y_score[num] = Y_valid_score[num][ensemble[-1], :]

                    # Perform n-fold cross validation to estimate performance of next best classifier
                    performances_temp = np.zeros((n_iter))
                    for n_crossval in range(0, n_iter):
                        # For each estimator, add the score to the ensemble and new ensemble performance
                        if iteration == 0:
                            # No y_score yet, so we need to build it instead of stacking
                            y_valid_score_new = Y_valid_score[n_crossval][sortedindices[iteration], :]
                        else:
                            # Stack scores of added model on top of previous scores and average
                            y_valid_score_new = np.mean(np.vstack((y_score[n_crossval], Y_valid_score[n_crossval][sortedindices[iteration], :])), axis=0)

                        perf = compute_performance(scoring, Y_valid_truth[n_crossval], y_valid_score_new)
                        performances_temp[n_crossval] = perf

                    # Check which ensemble should be in the ensemble to maximally improve
                    new_performance = np.mean(performances_temp)
                    performances_n_class.append(new_performance)
                    best_index = sortedindices[iteration]
                    iteration += 1

                # Select N_models for initialization
                new_performance = max(performances_n_class)
                N_models = performances_n_class.index(new_performance) + 1  # +1 due to python indexing
                ensemble = ensemble[0:N_models]
                best_performance = new_performance

                # Print the performance gain
                print("Ensembling best {}: {}.").format(scoring, str(best_performance))
                print("Single estimator best {}: {}.").format(scoring, str(single_estimator_performance))
                print('Ensemble consists of {} estimators {}.').format(str(len(ensemble)), str(ensemble))

            # Greedy selection  -----------------------------------------------
            # Initialize variables
            best_performance -= 1e-10
            iteration = 0

            # Go on adding to the ensemble untill we find the optimal performance
            if verbose:
                print("\n")
                print('Greedy selection.')
            while new_performance > best_performance:
                # Score is better, so expand ensemble and replace new best score
                if verbose:
                    print("Iteration: {}, best {}: {}.").format(str(iteration), scoring, str(new_performance))
                best_performance = new_performance

                if iteration > 1:
                    # Stack scores: not needed for first iteration
                    ensemble.append(best_index)
                    for num in range(0, n_iter):
                        y_score[num] = np.vstack((y_score[num], Y_valid_score[num][ensemble[-1], :]))

                elif iteration == 1:
                    if not initialize:
                        # Create y_score object for second iteration
                        single_estimator_performance = new_performance
                        ensemble.append(best_index)
                        for num in range(0, n_iter):
                            y_score[num] = Y_valid_score[num][ensemble[-1], :]
                    else:
                        # Stack scores: not needed when ensemble initialization is already used
                        ensemble.append(best_index)
                        for num in range(0, n_iter):
                            y_score[num] = np.vstack((y_score[num], Y_valid_score[num][ensemble[-1], :]))

                # Perform n-fold cross validation to estimate performance of each possible addition to ensemble
                performances_temp = np.zeros((n_iter, n_classifiers))
                for n_crossval in range(0, n_iter):
                    # For each estimator, add the score to the ensemble and new ensemble performance
                    for n_estimator in range(0, n_classifiers):
                        if iteration == 0:
                            # No y_score yet, so we need to build it instead of stacking
                            y_valid_score_new = Y_valid_score[n_crossval][n_estimator, :]
                        else:
                            # Stack scores of added model on top of previous scores and average
                            y_valid_score_new = np.mean(np.vstack((y_score[n_crossval], Y_valid_score[n_crossval][n_estimator, :])), axis=0)

                        perf = compute_performance(scoring, Y_valid_truth[n_crossval], y_valid_score_new)
                        performances_temp[n_crossval, n_estimator] = perf

                # Average performances over crossval
                performances_temp = list(np.mean(performances_temp, axis=0))

                # Check which ensemble should be in the ensemble to maximally improve
                new_performance = max(performances_temp)
                best_index = performances_temp.index(new_performance)
                iteration += 1

            # Print the performance gain
            print("Ensembling best {}: {}.").format(scoring, str(best_performance))
            print("Single estimator best {}: {}.").format(scoring, str(single_estimator_performance))
            print('Ensemble consists of {} estimators {}.').format(str(len(ensemble)), str(ensemble))
        else:
            print('[PREDICT WARNING] No valid ensemble method given: {}. Not ensembling').format(str(method))
            return self

        # Create the ensemble --------------------------------------------------
        # Create the ensemble trained on the full training set
        parameters_est = [parameters_est[i] for i in ensemble]
        parameters_all = [parameters_all[i] for i in ensemble]
        estimators = list()
        train = range(0, len(X_train))
        for p_est, p_all in zip(parameters_est, parameters_all):
            # Refit a SearchCV object with the provided parameters
            base_estimator = clone(base_estimator)

            base_estimator.refit_and_score(X_train, Y_train, p_all,
                                           p_est, train, train,
                                           verbose=False)

            estimators.append(base_estimator)

        self.ensemble = Ensemble(estimators)

        print("\n")
        return self


class BaseSearchCVfastr(BaseSearchCV):
    """Base class for hyper parameter search with cross-validation."""

    def _fit(self, X, y, groups, parameter_iterable):
        """Actual fitting,  performing the search over parameters."""

        base_estimator = clone(self.estimator)
        cv = check_cv(self.cv, y, classifier=is_classifier(base_estimator))
        self.scorer_ = check_scoring(self.estimator, scoring=self.scoring)

        X, y, groups = indexable(X, y, groups)
        n_splits = cv.get_n_splits(X, y, groups)
        if self.verbose > 0 and isinstance(parameter_iterable, Sized):
            n_candidates = len(parameter_iterable)
            print("Fitting {0} folds for each of {1} candidates, totalling"
                  " {2} fits".format(n_splits, n_candidates,
                                     n_candidates * n_splits))

        cv_iter = list(cv.split(X, y, groups))
        name = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10))
        tempfolder = os.path.join(fastr.config.mounts['tmp'], 'GS', name)
        if not os.path.exists(tempfolder):
            os.makedirs(tempfolder)

        # Create the parameter files
        parameters_temp = dict()
        for num, parameters in enumerate(parameter_iterable):

            parameters["Number"] = str(num)
            parameters_temp[str(num)] = parameters

        # Split the parameters files in equal parts
        keys = parameters_temp.keys()
        keys = chunks(keys, self.n_jobspercore)
        parameter_files = dict()
        for num, k in enumerate(keys):
            temp_dict = dict()
            for number in k:
                temp_dict[number] = parameters_temp[number]

            fname = ('settings_{}.json').format(str(num))
            sourcename = os.path.join(tempfolder, 'parameters', fname)
            if not os.path.exists(os.path.dirname(sourcename)):
                os.makedirs(os.path.dirname(sourcename))
            with open(sourcename, 'w') as fp:
                json.dump(temp_dict, fp, indent=4)

            parameter_files[str(num)] =\
                ('vfs://tmp/{}/{}/{}/{}').format('GS',
                                                 name,
                                                 'parameters',
                                                 fname)

        # Create test-train splits
        traintest_files = dict()
        # TODO: ugly nummering solution
        num = 0
        for train, test in cv_iter:
            source_labels = ['train', 'test']

            source_data = pd.Series([train, test],
                                    index=source_labels,
                                    name='Train-test data')

            fname = ('traintest_{}.hdf5').format(str(num))
            sourcename = os.path.join(tempfolder, 'traintest', fname)
            if not os.path.exists(os.path.dirname(sourcename)):
                os.makedirs(os.path.dirname(sourcename))
            traintest_files[str(num)] = ('vfs://tmp/{}/{}/{}/{}').format('GS',
                                                                         name,
                                                                         'traintest',
                                                                         fname)

            sourcelabel = ("Source Data Iteration {}").format(str(num))
            source_data.to_hdf(sourcename, sourcelabel)

            num += 1

        # Create the files containing the estimator and settings
        estimator_labels = ['base_estimator', 'X', 'y', 'scorer',
                            'verbose', 'fit_params', 'return_train_score',
                            'return_n_test_samples',
                            'return_times', 'return_parameters',
                            'error_score']

        estimator_data = pd.Series([clone(base_estimator), X, y, self.scorer_,
                                    self.verbose,
                                    self.fit_params, self.return_train_score,
                                    True, True, True,
                                    self.error_score],
                                   index=estimator_labels,
                                   name='estimator Data')
        fname = 'estimatordata.hdf5'
        estimatorname = os.path.join(tempfolder, fname)
        estimator_data.to_hdf(estimatorname, 'Estimator Data')

        estimatordata = ("vfs://tmp/{}/{}/{}").format('GS', name, fname)

        # Create the fastr network
        network = fastr.Network('PREDICT_GridSearch_' + name)
        estimator_data = network.create_source('HDF5', id_='estimator_source')
        traintest_data = network.create_source('HDF5', id_='traintest')
        parameter_data = network.create_source('JsonFile', id_='parameters')
        sink_output = network.create_sink('HDF5', id_='output')

        fitandscore = network.create_node('fitandscore', memory='8G', id_='fitandscore')
        fitandscore.inputs['estimatordata'].input_group = 'estimator'
        fitandscore.inputs['traintest'].input_group = 'traintest'
        fitandscore.inputs['parameters'].input_group = 'parameters'

        fitandscore.inputs['estimatordata'] = estimator_data.output
        fitandscore.inputs['traintest'] = traintest_data.output
        fitandscore.inputs['parameters'] = parameter_data.output
        sink_output.input = fitandscore.outputs['fittedestimator']

        source_data = {'estimator_source': estimatordata,
                       'traintest': traintest_files,
                       'parameters': parameter_files}
        sink_data = {'output': ("vfs://tmp/{}/{}/output_{{sample_id}}_{{cardinality}}{{ext}}").format('GS', name)}

        network.execute(source_data, sink_data,
                        tmpdir=os.path.join(tempfolder, 'tmp'),
                        execution_plugin=self.fastr_plugin)

        # Read in the output data once finished
        # TODO: expanding fastr url is probably a nicer way
        sink_files = glob.glob(os.path.join(fastr.config.mounts['tmp'], 'GS', name) + '/output*.hdf5')
        save_data = list()
        feature_labels = list()
        scalers = list()
        GroupSel = list()
        VarSel = list()
        SelectModel = list()
        Imputers = list()
        PCAs = list()
        StatisticalSel = list()
        for output in sink_files:
            data = pd.read_hdf(output)
            save_data.extend(list(data['RET']))
            feature_labels.extend(list(data['feature_labels']))
            scalers.extend(list(data['scaler']))
            GroupSel.extend(list(data['GroupSelection']))
            VarSel.extend(list(data['VarSelection']))
            SelectModel.extend(list(data['SelectModel']))
            Imputers.extend(list(data['Imputer']))
            PCAs.extend(list(data['PCA']))
            StatisticalSel.extend(list(data['StatisticalSel']))

        # if one choose to see train score, "out" will contain train score info
        try:
            if self.return_train_score:
                (train_scores, test_scores, test_sample_counts,
                 fit_time, score_time, parameters_est, parameters_all) =\
                  zip(*save_data)
            else:
                (test_scores, test_sample_counts,
                 fit_time, score_time, parameters_est, parameters_all) =\
                  zip(*save_data)
        except ValueError:
            message = ('Fitting classifiers has failed. The temporary' +
                       'results where not deleted and can be found in {}. ' +
                       'Probably your fitting and scoring failed: check out ' +
                       'the tmp/fitandscore folder within the tempfolder for' +
                       'the fastr job temporary results.').format(tempfolder)
            raise PREDICTexceptions.PREDICTValueError(message)

        # Remove the temporary folder used
        shutil.rmtree(tempfolder)

        # Create a dictionary from all the fitted objects
        fitted_objects = dict()
        fitted_objects['groupsel'] = GroupSel
        fitted_objects['imputer'] = Imputers
        fitted_objects['modelsel'] = SelectModel
        fitted_objects['varsel'] = VarSel
        fitted_objects['statisticalsel'] = StatisticalSel
        fitted_objects['scaler'] = scalers
        fitted_objects['pca'] = PCAs

        # Process the results of the fitting procedure
        self.process_fit(n_splits=n_splits,
                         parameters_est=parameters_est,
                         parameters_all=parameters_all,
                         fitted_objects=fitted_objects,
                         feature_labels=feature_labels,
                         test_sample_counts=test_sample_counts,
                         test_scores=test_scores,
                         train_scores=train_scores,
                         fit_time=fit_time,
                         score_time=score_time,
                         cv_iter=cv_iter,
                         base_estimator=base_estimator,
                         X=X, y=y)

        # return self


class RandomizedSearchCVfastr(BaseSearchCVfastr):
    """Randomized search on hyper parameters.

    RandomizedSearchCV implements a "fit" and a "score" method.
    It also implements "predict", "predict_proba", "decision_function",
    "transform" and "inverse_transform" if they are implemented in the
    estimator used.

    The parameters of the estimator used to apply these methods are optimized
    by cross-validated search over parameter settings.

    In contrast to GridSearchCV, not all parameter values are tried out, but
    rather a fixed number of parameter settings is sampled from the specified
    distributions. The number of parameter settings that are tried is
    given by n_iter.

    If all parameters are presented as a list,
    sampling without replacement is performed. If at least one parameter
    is given as a distribution, sampling with replacement is used.
    It is highly recommended to use continuous distributions for continuous
    parameters.

    Read more in the :ref:`User Guide <randomized_parameter_search>`.

    Parameters
    ----------
    estimator : estimator object.
        A object of that type is instantiated for each grid point.
        This is assumed to implement the scikit-learn estimator interface.
        Either estimator needs to provide a ``score`` function,
        or ``scoring`` must be passed.

    param_distributions : dict
        Dictionary with parameters names (string) as keys and distributions
        or lists of parameters to try. Distributions must provide a ``rvs``
        method for sampling (such as those from scipy.stats.distributions).
        If a list is given, it is sampled uniformly.

    n_iter : int, default=10
        Number of parameter settings that are sampled. n_iter trades
        off runtime vs quality of the solution.

    scoring : string, callable or None, default=None
        A string (see model evaluation documentation) or
        a scorer callable object / function with signature
        ``scorer(estimator, X, y)``.
        If ``None``, the ``score`` method of the estimator is used.

    fit_params : dict, optional
        Parameters to pass to the fit method.

    n_jobs : int, default=1
        Number of jobs to run in parallel.

    pre_dispatch : int, or string, optional
        Controls the number of jobs that get dispatched during parallel
        execution. Reducing this number can be useful to avoid an
        explosion of memory consumption when more jobs get dispatched
        than CPUs can process. This parameter can be:

            - None, in which case all the jobs are immediately
              created and spawned. Use this for lightweight and
              fast-running jobs, to avoid delays due to on-demand
              spawning of the jobs

            - An int, giving the exact number of total jobs that are
              spawned

            - A string, giving an expression as a function of n_jobs,
              as in '2*n_jobs'

    iid : boolean, default=True
        If True, the data is assumed to be identically distributed across
        the folds, and the loss minimized is the total loss per sample,
        and not the mean loss across the folds.

    cv : int, cross-validation generator or an iterable, optional
        Determines the cross-validation splitting strategy.
        Possible inputs for cv are:
          - None, to use the default 3-fold cross validation,
          - integer, to specify the number of folds in a `(Stratified)KFold`,
          - An object to be used as a cross-validation generator.
          - An iterable yielding train, test splits.

        For integer/None inputs, if the estimator is a classifier and ``y`` is
        either binary or multiclass, :class:`StratifiedKFold` is used. In all
        other cases, :class:`KFold` is used.

        Refer :ref:`User Guide <cross_validation>` for the various
        cross-validation strategies that can be used here.

    refit : boolean, default=True
        Refit the best estimator with the entire dataset.
        If "False", it is impossible to make predictions using
        this RandomizedSearchCV instance after fitting.

    verbose : integer
        Controls the verbosity: the higher, the more messages.

    random_state : int or RandomState
        Pseudo random number generator state used for random uniform sampling
        from lists of possible values instead of scipy.stats distributions.

    error_score : 'raise' (default) or numeric
        Value to assign to the score if an error occurs in estimator fitting.
        If set to 'raise', the error is raised. If a numeric value is given,
        FitFailedWarning is raised. This parameter does not affect the refit
        step, which will always raise the error.

    return_train_score : boolean, default=True
        If ``'False'``, the ``cv_results_`` attribute will not include training
        scores.

    Attributes
    ----------
    cv_results_ : dict of numpy (masked) ndarrays
        A dict with keys as column headers and values as columns, that can be
        imported into a pandas ``DataFrame``.

        For instance the below given table

        +--------------+-------------+-------------------+---+---------------+
        | param_kernel | param_gamma | split0_test_score |...|rank_test_score|
        +==============+=============+===================+===+===============+
        |    'rbf'     |     0.1     |        0.8        |...|       2       |
        +--------------+-------------+-------------------+---+---------------+
        |    'rbf'     |     0.2     |        0.9        |...|       1       |
        +--------------+-------------+-------------------+---+---------------+
        |    'rbf'     |     0.3     |        0.7        |...|       1       |
        +--------------+-------------+-------------------+---+---------------+

        will be represented by a ``cv_results_`` dict of::

            {
            'param_kernel' : masked_array(data = ['rbf', 'rbf', 'rbf'],
                                          mask = False),
            'param_gamma'  : masked_array(data = [0.1 0.2 0.3], mask = False),
            'split0_test_score'  : [0.8, 0.9, 0.7],
            'split1_test_score'  : [0.82, 0.5, 0.7],
            'mean_test_score'    : [0.81, 0.7, 0.7],
            'std_test_score'     : [0.02, 0.2, 0.],
            'rank_test_score'    : [3, 1, 1],
            'split0_train_score' : [0.8, 0.9, 0.7],
            'split1_train_score' : [0.82, 0.5, 0.7],
            'mean_train_score'   : [0.81, 0.7, 0.7],
            'std_train_score'    : [0.03, 0.03, 0.04],
            'mean_fit_time'      : [0.73, 0.63, 0.43, 0.49],
            'std_fit_time'       : [0.01, 0.02, 0.01, 0.01],
            'mean_score_time'    : [0.007, 0.06, 0.04, 0.04],
            'std_score_time'     : [0.001, 0.002, 0.003, 0.005],
            'params' : [{'kernel' : 'rbf', 'gamma' : 0.1}, ...],
            }

        NOTE that the key ``'params'`` is used to store a list of parameter
        settings dict for all the parameter candidates.

        The ``mean_fit_time``, ``std_fit_time``, ``mean_score_time`` and
        ``std_score_time`` are all in seconds.

    best_estimator_ : estimator
        Estimator that was chosen by the search, i.e. estimator
        which gave highest score (or smallest loss if specified)
        on the left out data. Not available if refit=False.

    best_score_ : float
        Score of best_estimator on the left out data.

    best_params_ : dict
        Parameter setting that gave the best results on the hold out data.

    best_index_ : int
        The index (of the ``cv_results_`` arrays) which corresponds to the best
        candidate parameter setting.

        The dict at ``search.cv_results_['params'][search.best_index_]`` gives
        the parameter setting for the best model, that gives the highest
        mean score (``search.best_score_``).

    scorer_ : function
        Scorer function used on the held out data to choose the best
        parameters for the model.

    n_splits_ : int
        The number of cross-validation splits (folds/iterations).

    Notes
    -----
    The parameters selected are those that maximize the score of the held-out
    data, according to the scoring parameter.

    If `n_jobs` was set to a value higher than one, the data is copied for each
    parameter setting(and not `n_jobs` times). This is done for efficiency
    reasons if individual jobs take very little time, but may raise errors if
    the dataset is large and not enough memory is available.  A workaround in
    this case is to set `pre_dispatch`. Then, the memory is copied only
    `pre_dispatch` many times. A reasonable value for `pre_dispatch` is `2 *
    n_jobs`.

    See Also
    --------
    :class:`GridSearchCV`:
        Does exhaustive search over a grid of parameters.

    :class:`ParameterSampler`:
        A generator over parameter settings, constructed from
        param_distributions.

    """

    def __init__(self, estimator, param_distributions={}, n_iter=10, scoring=None,
                 fit_params=None, n_jobs=1, iid=True, refit=True, cv=None,
                 verbose=0, pre_dispatch='2*n_jobs', random_state=None,
                 error_score='raise', return_train_score=True,
                 n_jobspercore=100, fastr_plugin=None):
        super(RandomizedSearchCVfastr, self).__init__(
             estimator=estimator, param_distributions=param_distributions, scoring=scoring, fit_params=fit_params,
             n_iter=n_iter, random_state=random_state, n_jobs=n_jobs, iid=iid, refit=refit, cv=cv, verbose=verbose,
             pre_dispatch=pre_dispatch, error_score=error_score,
             return_train_score=return_train_score,
             n_jobspercore=n_jobspercore, fastr_plugin=None)

    def fit(self, X, y=None, groups=None):
        """Run fit on the estimator with randomly drawn parameters.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]
            Training vector, where n_samples in the number of samples and
            n_features is the number of features.

        y : array-like, shape = [n_samples] or [n_samples, n_output], optional
            Target relative to X for classification or regression;
            None for unsupervised learning.

        groups : array-like, with shape (n_samples,), optional
            Group labels for the samples used while splitting the dataset into
            train/test set.
        """
        print("Fit: " + str(self.n_iter))
        sampled_params = ParameterSampler(self.param_distributions,
                                          self.n_iter,
                                          random_state=self.random_state)
        return self._fit(X, y, groups, sampled_params)


class BaseSearchCVJoblib(BaseSearchCV):
    """Base class for hyper parameter search with cross-validation."""

    def _fit(self, X, y, groups, parameter_iterable):
        """Actual fitting,  performing the search over parameters."""

        base_estimator = clone(self.estimator)
        cv = check_cv(self.cv, y, classifier=is_classifier(base_estimator))
        self.scorer_ = check_scoring(self.estimator, scoring=self.scoring)

        X, y, groups = indexable(X, y, groups)
        n_splits = cv.get_n_splits(X, y, groups)
        if self.verbose > 0 and isinstance(parameter_iterable, Sized):
            n_candidates = len(parameter_iterable)
            print("Fitting {0} folds for each of {1} candidates, totalling"
                  " {2} fits".format(n_splits, n_candidates,
                                     n_candidates * n_splits))

        pre_dispatch = self.pre_dispatch
        cv_iter = list(cv.split(X, y, groups))

        out = Parallel(
            n_jobs=self.n_jobs, verbose=self.verbose,
            pre_dispatch=pre_dispatch
        )(delayed(fit_and_score)(clone(base_estimator), X, y, self.scorer_,
                                 train, test, parameters,
                                 fit_params=self.fit_params,
                                 return_train_score=self.return_train_score,
                                 return_n_test_samples=True,
                                 return_times=True, return_parameters=True,
                                 error_score=self.error_score,
                                 verbose=self.verbose)
          for parameters in parameter_iterable
          for train, test in cv_iter)
        (save_data, GroupSel, VarSel, SelectModel, feature_labels, scalers,
            Imputers, PCAs, StatisticalSel) = zip(*out)

        # if one choose to see train score, "out" will contain train score info
        if self.return_train_score:
            (train_scores, test_scores, test_sample_counts,
             fit_time, score_time, parameters_est, parameters_all) =\
              zip(*save_data)
        else:
            (test_scores, test_sample_counts,
             fit_time, score_time, parameters_est, parameters_all) =\
              zip(*save_data)

        # Create a dictionary from all the fitted objects
        fitted_objects = dict()
        fitted_objects['groupsel'] = GroupSel
        fitted_objects['imputer'] = Imputers
        fitted_objects['modelsel'] = SelectModel
        fitted_objects['varsel'] = VarSel
        fitted_objects['statisticalsel'] = StatisticalSel
        fitted_objects['scaler'] = scalers
        fitted_objects['pca'] = PCAs

        self.process_fit(n_splits=n_splits,
                         parameters_est=parameters_est,
                         parameters_all=parameters_all,
                         fitted_objects=fitted_objects,
                         feature_labels=feature_labels,
                         test_sample_counts=test_sample_counts,
                         test_scores=test_scores,
                         train_scores=train_scores,
                         fit_time=fit_time,
                         score_time=score_time,
                         cv_iter=cv_iter,
                         base_estimator=base_estimator,
                         X=X, y=y)

        # return self


class GridSearchCVfastr(BaseSearchCVfastr):
    """Exhaustive search over specified parameter values for an estimator.

    Important members are fit, predict.

    GridSearchCV implements a "fit" and a "score" method.
    It also implements "predict", "predict_proba", "decision_function",
    "transform" and "inverse_transform" if they are implemented in the
    estimator used.

    The parameters of the estimator used to apply these methods are optimized
    by cross-validated grid-search over a parameter grid.

    Read more in the :ref:`User Guide <grid_search>`.

    Parameters
    ----------
    estimator : estimator object.
        This is assumed to implement the scikit-learn estimator interface.
        Either estimator needs to provide a ``score`` function,
        or ``scoring`` must be passed.

    param_grid : dict or list of dictionaries
        Dictionary with parameters names (string) as keys and lists of
        parameter settings to try as values, or a list of such
        dictionaries, in which case the grids spanned by each dictionary
        in the list are explored. This enables searching over any sequence
        of parameter settings.

    scoring : string, callable or None, default=None
        A string (see model evaluation documentation) or
        a scorer callable object / function with signature
        ``scorer(estimator, X, y)``.
        If ``None``, the ``score`` method of the estimator is used.

    fit_params : dict, optional
        Parameters to pass to the fit method.

    n_jobs : int, default=1
        Number of jobs to run in parallel.

    pre_dispatch : int, or string, optional
        Controls the number of jobs that get dispatched during parallel
        execution. Reducing this number can be useful to avoid an
        explosion of memory consumption when more jobs get dispatched
        than CPUs can process. This parameter can be:

            - None, in which case all the jobs are immediately
              created and spawned. Use this for lightweight and
              fast-running jobs, to avoid delays due to on-demand
              spawning of the jobs

            - An int, giving the exact number of total jobs that are
              spawned

            - A string, giving an expression as a function of n_jobs,
              as in '2*n_jobs'

    iid : boolean, default=True
        If True, the data is assumed to be identically distributed across
        the folds, and the loss minimized is the total loss per sample,
        and not the mean loss across the folds.

    cv : int, cross-validation generator or an iterable, optional
        Determines the cross-validation splitting strategy.
        Possible inputs for cv are:
          - None, to use the default 3-fold cross validation,
          - integer, to specify the number of folds in a `(Stratified)KFold`,
          - An object to be used as a cross-validation generator.
          - An iterable yielding train, test splits.

        For integer/None inputs, if the estimator is a classifier and ``y`` is
        either binary or multiclass, :class:`StratifiedKFold` is used. In all
        other cases, :class:`KFold` is used.

        Refer :ref:`User Guide <cross_validation>` for the various
        cross-validation strategies that can be used here.

    refit : boolean, default=True
        Refit the best estimator with the entire dataset.
        If "False", it is impossible to make predictions using
        this GridSearchCV instance after fitting.

    verbose : integer
        Controls the verbosity: the higher, the more messages.

    error_score : 'raise' (default) or numeric
        Value to assign to the score if an error occurs in estimator fitting.
        If set to 'raise', the error is raised. If a numeric value is given,
        FitFailedWarning is raised. This parameter does not affect the refit
        step, which will always raise the error.

    return_train_score : boolean, default=True
        If ``'False'``, the ``cv_results_`` attribute will not include training
        scores.


    Examples
    --------
    >>> from sklearn import svm, datasets
    >>> from sklearn.model_selection import GridSearchCV
    >>> iris = datasets.load_iris()
    >>> parameters = {'kernel':('linear', 'rbf'), 'C':[1, 10]}
    >>> svr = svm.SVC()
    >>> clf = GridSearchCV(svr, parameters)
    >>> clf.fit(iris.data, iris.target)
    ...                             # doctest: +NORMALIZE_WHITESPACE +ELLIPSIS
    GridSearchCV(cv=None, error_score=...,
           estimator=SVC(C=1.0, cache_size=..., class_weight=..., coef0=...,
                         decision_function_shape=None, degree=..., gamma=...,
                         kernel='rbf', max_iter=-1, probability=False,
                         random_state=None, shrinking=True, tol=...,
                         verbose=False),
           fit_params={}, iid=..., n_jobs=1,
           param_grid=..., pre_dispatch=..., refit=..., return_train_score=...,
           scoring=..., verbose=...)
    >>> sorted(clf.cv_results_.keys())
    ...                             # doctest: +NORMALIZE_WHITESPACE +ELLIPSIS
    ['mean_fit_time', 'mean_score_time', 'mean_test_score',...
     'mean_train_score', 'param_C', 'param_kernel', 'params',...
     'rank_test_score', 'split0_test_score',...
     'split0_train_score', 'split1_test_score', 'split1_train_score',...
     'split2_test_score', 'split2_train_score',...
     'std_fit_time', 'std_score_time', 'std_test_score', 'std_train_score'...]

    Attributes
    ----------
    cv_results_ : dict of numpy (masked) ndarrays
        A dict with keys as column headers and values as columns, that can be
        imported into a pandas ``DataFrame``.

        For instance the below given table

        +------------+-----------+------------+-----------------+---+---------+
        |param_kernel|param_gamma|param_degree|split0_test_score|...|rank_....|
        +============+===========+============+=================+===+=========+
        |  'poly'    |     --    |      2     |        0.8      |...|    2    |
        +------------+-----------+------------+-----------------+---+---------+
        |  'poly'    |     --    |      3     |        0.7      |...|    4    |
        +------------+-----------+------------+-----------------+---+---------+
        |  'rbf'     |     0.1   |     --     |        0.8      |...|    3    |
        +------------+-----------+------------+-----------------+---+---------+
        |  'rbf'     |     0.2   |     --     |        0.9      |...|    1    |
        +------------+-----------+------------+-----------------+---+---------+

        will be represented by a ``cv_results_`` dict of::

            {
            'param_kernel': masked_array(data = ['poly', 'poly', 'rbf', 'rbf'],
                                         mask = [False False False False]...)
            'param_gamma': masked_array(data = [-- -- 0.1 0.2],
                                        mask = [ True  True False False]...),
            'param_degree': masked_array(data = [2.0 3.0 -- --],
                                         mask = [False False  True  True]...),
            'split0_test_score'  : [0.8, 0.7, 0.8, 0.9],
            'split1_test_score'  : [0.82, 0.5, 0.7, 0.78],
            'mean_test_score'    : [0.81, 0.60, 0.75, 0.82],
            'std_test_score'     : [0.02, 0.01, 0.03, 0.03],
            'rank_test_score'    : [2, 4, 3, 1],
            'split0_train_score' : [0.8, 0.9, 0.7],
            'split1_train_score' : [0.82, 0.5, 0.7],
            'mean_train_score'   : [0.81, 0.7, 0.7],
            'std_train_score'    : [0.03, 0.03, 0.04],
            'mean_fit_time'      : [0.73, 0.63, 0.43, 0.49],
            'std_fit_time'       : [0.01, 0.02, 0.01, 0.01],
            'mean_score_time'    : [0.007, 0.06, 0.04, 0.04],
            'std_score_time'     : [0.001, 0.002, 0.003, 0.005],
            'params'             : [{'kernel': 'poly', 'degree': 2}, ...],
            }

        NOTE that the key ``'params'`` is used to store a list of parameter
        settings dict for all the parameter candidates.

        The ``mean_fit_time``, ``std_fit_time``, ``mean_score_time`` and
        ``std_score_time`` are all in seconds.

    best_estimator_ : estimator
        Estimator that was chosen by the search, i.e. estimator
        which gave highest score (or smallest loss if specified)
        on the left out data. Not available if refit=False.

    best_score_ : float
        Score of best_estimator on the left out data.

    best_params_ : dict
        Parameter setting that gave the best results on the hold out data.

    best_index_ : int
        The index (of the ``cv_results_`` arrays) which corresponds to the best
        candidate parameter setting.

        The dict at ``search.cv_results_['params'][search.best_index_]`` gives
        the parameter setting for the best model, that gives the highest
        mean score (``search.best_score_``).

    scorer_ : function
        Scorer function used on the held out data to choose the best
        parameters for the model.

    n_splits_ : int
        The number of cross-validation splits (folds/iterations).

    Notes
    ------
    The parameters selected are those that maximize the score of the left out
    data, unless an explicit score is passed in which case it is used instead.

    If `n_jobs` was set to a value higher than one, the data is copied for each
    point in the grid (and not `n_jobs` times). This is done for efficiency
    reasons if individual jobs take very little time, but may raise errors if
    the dataset is large and not enough memory is available.  A workaround in
    this case is to set `pre_dispatch`. Then, the memory is copied only
    `pre_dispatch` many times. A reasonable value for `pre_dispatch` is `2 *
    n_jobs`.

    See Also
    ---------
    :class:`ParameterGrid`:
        generates all the combinations of a hyperparameter grid.

    :func:`sklearn.model_selection.train_test_split`:
        utility function to split the data into a development set usable
        for fitting a GridSearchCV instance and an evaluation set for
        its final evaluation.

    :func:`sklearn.metrics.make_scorer`:
        Make a scorer from a performance metric or loss function.

    """

    def __init__(self, estimator, param_grid, scoring=None, fit_params=None,
                 n_jobs=1, iid=True, refit=True, cv=None, verbose=0,
                 pre_dispatch='2*n_jobs', error_score='raise',
                 return_train_score=True):
        super(GridSearchCVfastr, self).__init__(
            estimator=estimator, scoring=scoring, fit_params=fit_params,
            n_jobs=n_jobs, iid=iid, refit=refit, cv=cv, verbose=verbose,
            pre_dispatch=pre_dispatch, error_score=error_score,
            return_train_score=return_train_score, fastr_plugin=None)
        self.param_grid = param_grid
        _check_param_grid(param_grid)

    def fit(self, X, y=None, groups=None):
        """Run fit with all sets of parameters.

        Parameters
        ----------

        X : array-like, shape = [n_samples, n_features]
            Training vector, where n_samples is the number of samples and
            n_features is the number of features.

        y : array-like, shape = [n_samples] or [n_samples, n_output], optional
            Target relative to X for classification or regression;
            None for unsupervised learning.

        groups : array-like, with shape (n_samples,), optional
            Group labels for the samples used while splitting the dataset into
            train/test set.
        """
        return self._fit(X, y, groups, ParameterGrid(self.param_grid))


class RandomizedSearchCVJoblib(BaseSearchCVJoblib):
    """Randomized search on hyper parameters.

    RandomizedSearchCV implements a "fit" and a "score" method.
    It also implements "predict", "predict_proba", "decision_function",
    "transform" and "inverse_transform" if they are implemented in the
    estimator used.

    The parameters of the estimator used to apply these methods are optimized
    by cross-validated search over parameter settings.

    In contrast to GridSearchCV, not all parameter values are tried out, but
    rather a fixed number of parameter settings is sampled from the specified
    distributions. The number of parameter settings that are tried is
    given by n_iter.

    If all parameters are presented as a list,
    sampling without replacement is performed. If at least one parameter
    is given as a distribution, sampling with replacement is used.
    It is highly recommended to use continuous distributions for continuous
    parameters.

    Read more in the :ref:`User Guide <randomized_parameter_search>`.

    Parameters
    ----------
    estimator : estimator object.
        A object of that type is instantiated for each grid point.
        This is assumed to implement the scikit-learn estimator interface.
        Either estimator needs to provide a ``score`` function,
        or ``scoring`` must be passed.

    param_distributions : dict
        Dictionary with parameters names (string) as keys and distributions
        or lists of parameters to try. Distributions must provide a ``rvs``
        method for sampling (such as those from scipy.stats.distributions).
        If a list is given, it is sampled uniformly.

    n_iter : int, default=10
        Number of parameter settings that are sampled. n_iter trades
        off runtime vs quality of the solution.

    scoring : string, callable or None, default=None
        A string (see model evaluation documentation) or
        a scorer callable object / function with signature
        ``scorer(estimator, X, y)``.
        If ``None``, the ``score`` method of the estimator is used.

    fit_params : dict, optional
        Parameters to pass to the fit method.

    n_jobs : int, default=1
        Number of jobs to run in parallel.

    pre_dispatch : int, or string, optional
        Controls the number of jobs that get dispatched during parallel
        execution. Reducing this number can be useful to avoid an
        explosion of memory consumption when more jobs get dispatched
        than CPUs can process. This parameter can be:

            - None, in which case all the jobs are immediately
              created and spawned. Use this for lightweight and
              fast-running jobs, to avoid delays due to on-demand
              spawning of the jobs

            - An int, giving the exact number of total jobs that are
              spawned

            - A string, giving an expression as a function of n_jobs,
              as in '2*n_jobs'

    iid : boolean, default=True
        If True, the data is assumed to be identically distributed across
        the folds, and the loss minimized is the total loss per sample,
        and not the mean loss across the folds.

    cv : int, cross-validation generator or an iterable, optional
        Determines the cross-validation splitting strategy.
        Possible inputs for cv are:
          - None, to use the default 3-fold cross validation,
          - integer, to specify the number of folds in a `(Stratified)KFold`,
          - An object to be used as a cross-validation generator.
          - An iterable yielding train, test splits.

        For integer/None inputs, if the estimator is a classifier and ``y`` is
        either binary or multiclass, :class:`StratifiedKFold` is used. In all
        other cases, :class:`KFold` is used.

        Refer :ref:`User Guide <cross_validation>` for the various
        cross-validation strategies that can be used here.

    refit : boolean, default=True
        Refit the best estimator with the entire dataset.
        If "False", it is impossible to make predictions using
        this RandomizedSearchCV instance after fitting.

    verbose : integer
        Controls the verbosity: the higher, the more messages.

    random_state : int or RandomState
        Pseudo random number generator state used for random uniform sampling
        from lists of possible values instead of scipy.stats distributions.

    error_score : 'raise' (default) or numeric
        Value to assign to the score if an error occurs in estimator fitting.
        If set to 'raise', the error is raised. If a numeric value is given,
        FitFailedWarning is raised. This parameter does not affect the refit
        step, which will always raise the error.

    return_train_score : boolean, default=True
        If ``'False'``, the ``cv_results_`` attribute will not include training
        scores.

    Attributes
    ----------
    cv_results_ : dict of numpy (masked) ndarrays
        A dict with keys as column headers and values as columns, that can be
        imported into a pandas ``DataFrame``.

        For instance the below given table

        +--------------+-------------+-------------------+---+---------------+
        | param_kernel | param_gamma | split0_test_score |...|rank_test_score|
        +==============+=============+===================+===+===============+
        |    'rbf'     |     0.1     |        0.8        |...|       2       |
        +--------------+-------------+-------------------+---+---------------+
        |    'rbf'     |     0.2     |        0.9        |...|       1       |
        +--------------+-------------+-------------------+---+---------------+
        |    'rbf'     |     0.3     |        0.7        |...|       1       |
        +--------------+-------------+-------------------+---+---------------+

        will be represented by a ``cv_results_`` dict of::

            {
            'param_kernel' : masked_array(data = ['rbf', 'rbf', 'rbf'],
                                          mask = False),
            'param_gamma'  : masked_array(data = [0.1 0.2 0.3], mask = False),
            'split0_test_score'  : [0.8, 0.9, 0.7],
            'split1_test_score'  : [0.82, 0.5, 0.7],
            'mean_test_score'    : [0.81, 0.7, 0.7],
            'std_test_score'     : [0.02, 0.2, 0.],
            'rank_test_score'    : [3, 1, 1],
            'split0_train_score' : [0.8, 0.9, 0.7],
            'split1_train_score' : [0.82, 0.5, 0.7],
            'mean_train_score'   : [0.81, 0.7, 0.7],
            'std_train_score'    : [0.03, 0.03, 0.04],
            'mean_fit_time'      : [0.73, 0.63, 0.43, 0.49],
            'std_fit_time'       : [0.01, 0.02, 0.01, 0.01],
            'mean_score_time'    : [0.007, 0.06, 0.04, 0.04],
            'std_score_time'     : [0.001, 0.002, 0.003, 0.005],
            'params' : [{'kernel' : 'rbf', 'gamma' : 0.1}, ...],
            }

        NOTE that the key ``'params'`` is used to store a list of parameter
        settings dict for all the parameter candidates.

        The ``mean_fit_time``, ``std_fit_time``, ``mean_score_time`` and
        ``std_score_time`` are all in seconds.

    best_estimator_ : estimator
        Estimator that was chosen by the search, i.e. estimator
        which gave highest score (or smallest loss if specified)
        on the left out data. Not available if refit=False.

    best_score_ : float
        Score of best_estimator on the left out data.

    best_params_ : dict
        Parameter setting that gave the best results on the hold out data.

    best_index_ : int
        The index (of the ``cv_results_`` arrays) which corresponds to the best
        candidate parameter setting.

        The dict at ``search.cv_results_['params'][search.best_index_]`` gives
        the parameter setting for the best model, that gives the highest
        mean score (``search.best_score_``).

    scorer_ : function
        Scorer function used on the held out data to choose the best
        parameters for the model.

    n_splits_ : int
        The number of cross-validation splits (folds/iterations).

    Notes
    -----
    The parameters selected are those that maximize the score of the held-out
    data, according to the scoring parameter.

    If `n_jobs` was set to a value higher than one, the data is copied for each
    parameter setting(and not `n_jobs` times). This is done for efficiency
    reasons if individual jobs take very little time, but may raise errors if
    the dataset is large and not enough memory is available.  A workaround in
    this case is to set `pre_dispatch`. Then, the memory is copied only
    `pre_dispatch` many times. A reasonable value for `pre_dispatch` is `2 *
    n_jobs`.

    See Also
    --------
    :class:`GridSearchCV`:
        Does exhaustive search over a grid of parameters.

    :class:`ParameterSampler`:
        A generator over parameter settins, constructed from
        param_distributions.

    """

    def __init__(self, estimator, param_distributions={}, n_iter=10, scoring=None,
                 fit_params=None, n_jobs=1, iid=True, refit=True, cv=None,
                 verbose=0, pre_dispatch='2*n_jobs', random_state=None,
                 error_score='raise', return_train_score=True,
                 n_jobspercore=100):
        super(RandomizedSearchCVJoblib, self).__init__(
             estimator=estimator, param_distributions=param_distributions,
             n_iter=n_iter, scoring=scoring, fit_params=fit_params,
             n_jobs=n_jobs, iid=iid, refit=refit, cv=cv, verbose=verbose,
             pre_dispatch=pre_dispatch, error_score=error_score,
             return_train_score=return_train_score,
             n_jobspercore=n_jobspercore, random_state=random_state)

    def fit(self, X, y=None, groups=None):
        """Run fit on the estimator with randomly drawn parameters.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_features]
            Training vector, where n_samples in the number of samples and
            n_features is the number of features.

        y : array-like, shape = [n_samples] or [n_samples, n_output], optional
            Target relative to X for classification or regression;
            None for unsupervised learning.

        groups : array-like, with shape (n_samples,), optional
            Group labels for the samples used while splitting the dataset into
            train/test set.
        """
        sampled_params = ParameterSampler(self.param_distributions,
                                          self.n_iter,
                                          random_state=self.random_state)
        return self._fit(X, y, groups, sampled_params)


class GridSearchCVJoblib(BaseSearchCVJoblib):
    """Exhaustive search over specified parameter values for an estimator.

    Important members are fit, predict.

    GridSearchCV implements a "fit" and a "score" method.
    It also implements "predict", "predict_proba", "decision_function",
    "transform" and "inverse_transform" if they are implemented in the
    estimator used.

    The parameters of the estimator used to apply these methods are optimized
    by cross-validated grid-search over a parameter grid.

    Read more in the :ref:`User Guide <grid_search>`.

    Parameters
    ----------
    estimator : estimator object.
        This is assumed to implement the scikit-learn estimator interface.
        Either estimator needs to provide a ``score`` function,
        or ``scoring`` must be passed.

    param_grid : dict or list of dictionaries
        Dictionary with parameters names (string) as keys and lists of
        parameter settings to try as values, or a list of such
        dictionaries, in which case the grids spanned by each dictionary
        in the list are explored. This enables searching over any sequence
        of parameter settings.

    scoring : string, callable or None, default=None
        A string (see model evaluation documentation) or
        a scorer callable object / function with signature
        ``scorer(estimator, X, y)``.
        If ``None``, the ``score`` method of the estimator is used.

    fit_params : dict, optional
        Parameters to pass to the fit method.

    n_jobs : int, default=1
        Number of jobs to run in parallel.

    pre_dispatch : int, or string, optional
        Controls the number of jobs that get dispatched during parallel
        execution. Reducing this number can be useful to avoid an
        explosion of memory consumption when more jobs get dispatched
        than CPUs can process. This parameter can be:

            - None, in which case all the jobs are immediately
              created and spawned. Use this for lightweight and
              fast-running jobs, to avoid delays due to on-demand
              spawning of the jobs

            - An int, giving the exact number of total jobs that are
              spawned

            - A string, giving an expression as a function of n_jobs,
              as in '2*n_jobs'

    iid : boolean, default=True
        If True, the data is assumed to be identically distributed across
        the folds, and the loss minimized is the total loss per sample,
        and not the mean loss across the folds.

    cv : int, cross-validation generator or an iterable, optional
        Determines the cross-validation splitting strategy.
        Possible inputs for cv are:
          - None, to use the default 3-fold cross validation,
          - integer, to specify the number of folds in a `(Stratified)KFold`,
          - An object to be used as a cross-validation generator.
          - An iterable yielding train, test splits.

        For integer/None inputs, if the estimator is a classifier and ``y`` is
        either binary or multiclass, :class:`StratifiedKFold` is used. In all
        other cases, :class:`KFold` is used.

        Refer :ref:`User Guide <cross_validation>` for the various
        cross-validation strategies that can be used here.

    refit : boolean, default=True
        Refit the best estimator with the entire dataset.
        If "False", it is impossible to make predictions using
        this GridSearchCV instance after fitting.

    verbose : integer
        Controls the verbosity: the higher, the more messages.

    error_score : 'raise' (default) or numeric
        Value to assign to the score if an error occurs in estimator fitting.
        If set to 'raise', the error is raised. If a numeric value is given,
        FitFailedWarning is raised. This parameter does not affect the refit
        step, which will always raise the error.

    return_train_score : boolean, default=True
        If ``'False'``, the ``cv_results_`` attribute will not include training
        scores.


    Examples
    --------
    >>> from sklearn import svm, datasets
    >>> from sklearn.model_selection import GridSearchCV
    >>> iris = datasets.load_iris()
    >>> parameters = {'kernel':('linear', 'rbf'), 'C':[1, 10]}
    >>> svr = svm.SVC()
    >>> clf = GridSearchCV(svr, parameters)
    >>> clf.fit(iris.data, iris.target)
    ...                             # doctest: +NORMALIZE_WHITESPACE +ELLIPSIS
    GridSearchCV(cv=None, error_score=...,
           estimator=SVC(C=1.0, cache_size=..., class_weight=..., coef0=...,
                         decision_function_shape=None, degree=..., gamma=...,
                         kernel='rbf', max_iter=-1, probability=False,
                         random_state=None, shrinking=True, tol=...,
                         verbose=False),
           fit_params={}, iid=..., n_jobs=1,
           param_grid=..., pre_dispatch=..., refit=..., return_train_score=...,
           scoring=..., verbose=...)
    >>> sorted(clf.cv_results_.keys())
    ...                             # doctest: +NORMALIZE_WHITESPACE +ELLIPSIS
    ['mean_fit_time', 'mean_score_time', 'mean_test_score',...
     'mean_train_score', 'param_C', 'param_kernel', 'params',...
     'rank_test_score', 'split0_test_score',...
     'split0_train_score', 'split1_test_score', 'split1_train_score',...
     'split2_test_score', 'split2_train_score',...
     'std_fit_time', 'std_score_time', 'std_test_score', 'std_train_score'...]

    Attributes
    ----------
    cv_results_ : dict of numpy (masked) ndarrays
        A dict with keys as column headers and values as columns, that can be
        imported into a pandas ``DataFrame``.

        For instance the below given table

        +------------+-----------+------------+-----------------+---+---------+
        |param_kernel|param_gamma|param_degree|split0_test_score|...|rank_....|
        +============+===========+============+=================+===+=========+
        |  'poly'    |     --    |      2     |        0.8      |...|    2    |
        +------------+-----------+------------+-----------------+---+---------+
        |  'poly'    |     --    |      3     |        0.7      |...|    4    |
        +------------+-----------+------------+-----------------+---+---------+
        |  'rbf'     |     0.1   |     --     |        0.8      |...|    3    |
        +------------+-----------+------------+-----------------+---+---------+
        |  'rbf'     |     0.2   |     --     |        0.9      |...|    1    |
        +------------+-----------+------------+-----------------+---+---------+

        will be represented by a ``cv_results_`` dict of::

            {
            'param_kernel': masked_array(data = ['poly', 'poly', 'rbf', 'rbf'],
                                         mask = [False False False False]...)
            'param_gamma': masked_array(data = [-- -- 0.1 0.2],
                                        mask = [ True  True False False]...),
            'param_degree': masked_array(data = [2.0 3.0 -- --],
                                         mask = [False False  True  True]...),
            'split0_test_score'  : [0.8, 0.7, 0.8, 0.9],
            'split1_test_score'  : [0.82, 0.5, 0.7, 0.78],
            'mean_test_score'    : [0.81, 0.60, 0.75, 0.82],
            'std_test_score'     : [0.02, 0.01, 0.03, 0.03],
            'rank_test_score'    : [2, 4, 3, 1],
            'split0_train_score' : [0.8, 0.9, 0.7],
            'split1_train_score' : [0.82, 0.5, 0.7],
            'mean_train_score'   : [0.81, 0.7, 0.7],
            'std_train_score'    : [0.03, 0.03, 0.04],
            'mean_fit_time'      : [0.73, 0.63, 0.43, 0.49],
            'std_fit_time'       : [0.01, 0.02, 0.01, 0.01],
            'mean_score_time'    : [0.007, 0.06, 0.04, 0.04],
            'std_score_time'     : [0.001, 0.002, 0.003, 0.005],
            'params'             : [{'kernel': 'poly', 'degree': 2}, ...],
            }

        NOTE that the key ``'params'`` is used to store a list of parameter
        settings dict for all the parameter candidates.

        The ``mean_fit_time``, ``std_fit_time``, ``mean_score_time`` and
        ``std_score_time`` are all in seconds.

    best_estimator_ : estimator
        Estimator that was chosen by the search, i.e. estimator
        which gave highest score (or smallest loss if specified)
        on the left out data. Not available if refit=False.

    best_score_ : float
        Score of best_estimator on the left out data.

    best_params_ : dict
        Parameter setting that gave the best results on the hold out data.

    best_index_ : int
        The index (of the ``cv_results_`` arrays) which corresponds to the best
        candidate parameter setting.

        The dict at ``search.cv_results_['params'][search.best_index_]`` gives
        the parameter setting for the best model, that gives the highest
        mean score (``search.best_score_``).

    scorer_ : function
        Scorer function used on the held out data to choose the best
        parameters for the model.

    n_splits_ : int
        The number of cross-validation splits (folds/iterations).

    Notes
    ------
    The parameters selected are those that maximize the score of the left out
    data, unless an explicit score is passed in which case it is used instead.

    If `n_jobs` was set to a value higher than one, the data is copied for each
    point in the grid (and not `n_jobs` times). This is done for efficiency
    reasons if individual jobs take very little time, but may raise errors if
    the dataset is large and not enough memory is available.  A workaround in
    this case is to set `pre_dispatch`. Then, the memory is copied only
    `pre_dispatch` many times. A reasonable value for `pre_dispatch` is `2 *
    n_jobs`.

    See Also
    ---------
    :class:`ParameterGrid`:
        generates all the combinations of a hyperparameter grid.

    :func:`sklearn.model_selection.train_test_split`:
        utility function to split the data into a development set usable
        for fitting a GridSearchCV instance and an evaluation set for
        its final evaluation.

    :func:`sklearn.metrics.make_scorer`:
        Make a scorer from a performance metric or loss function.

    """

    def __init__(self, estimator, param_grid, scoring=None, fit_params=None,
                 n_jobs=1, iid=True, refit=True, cv=None, verbose=0,
                 pre_dispatch='2*n_jobs', error_score='raise',
                 return_train_score=True):
        super(GridSearchCVJoblib, self).__init__(
            estimator=estimator, scoring=scoring, fit_params=fit_params,
            n_jobs=n_jobs, iid=iid, refit=refit, cv=cv, verbose=verbose,
            pre_dispatch=pre_dispatch, error_score=error_score,
            return_train_score=return_train_score)
        self.param_grid = param_grid
        _check_param_grid(param_grid)

    def fit(self, X, y=None, groups=None):
        """Run fit with all sets of parameters.

        Parameters
        ----------

        X : array-like, shape = [n_samples, n_features]
            Training vector, where n_samples is the number of samples and
            n_features is the number of features.

        y : array-like, shape = [n_samples] or [n_samples, n_output], optional
            Target relative to X for classification or regression;
            None for unsupervised learning.

        groups : array-like, with shape (n_samples,), optional
            Group labels for the samples used while splitting the dataset into
            train/test set.
        """
        return self._fit(X, y, groups, ParameterGrid(self.param_grid))
