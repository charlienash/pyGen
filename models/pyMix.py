"""Probabilistic principal components analysis (PPCA)

A generative latent linear variable model.

PPCA assumes that the observed data is generated by linearly transforming a
number of latent variables and then adding spherical Gaussian noise. The
latent variables are drawn from a standard Gaussian distribution.

This implementation is based on David Barber's Matlab implementation:
http://web4.cs.ucl.ac.uk/staff/D.Barber/pmwiki/pmwiki.php?n=Main.Software

This implementation uses the EM algorithm to handle missing data.
"""

# Author: Charlie Nash <charlie.tc.nash@gmail.com>

import numpy as np
import numpy.random as rd
#from numba import jit
from random import seed
#import GenModel
from util import _mv_gaussian_pdf, _get_rand_cov_mat
from scipy.stats import multivariate_normal as mvn
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA, FactorAnalysis


class GMM():
    """Probabilistic principal components analysis (PPCA).

    A generative latent linear variable model.

    PPCA assumes that the observed data is generated by linearly transforming a
    number of latent variables and then adding spherical Gaussian noise. The
    latent variables are drawn from a standard Gaussian distribution.

    The parameters of the model are the transformation matrix (principal
    components) the mean, and the noise variance.

    PPCA performs maximum likelihood or MAP estimation of the model parameters using
    the expectation-maximisation algorithm (EM).

    Attributes
    ----------

    latentDim : int
        Dimensionality of latent space. The number of variables that are
        transformed by the principal components to the data space.

    components : array, [latentDim, nFeatures]
        Transformation matrix parameter.

    bias: array, [nFeatures]
        Bias parameter.

    noiseVariance : float
        Noise variance parameter. Variance of noise that is added to linearly
        transformed latent variables to generate data.

    standardize : bool, optional
        When True, the mean is subtracted from the data, and each feature is
        divided by it's standard deviation so that the mean and variance of
        the transformed features are 0 and 1 respectively.

    componentPrior : float >= 0
        Gaussian component matrix hyperparameter. If > 0 then a Gaussian prior
        is applied to each column of the component matrix with covariance
        componentPrior^-1 * noiseVariance. This has the effect
        of regularising the component matrix.

    tol : float
        Stopping tolerance for EM algorithm

    maxIter : int
        Maximum number of iterations for EM algorithm

    Notes
    -----

    TODO

    Examples
    --------

    TODO
    """
    def __init__(self, n_components, tol=1e-3, max_iter=1000, random_state=0, 
                  verbose=True):
        self.tol = tol
        self.max_iter = max_iter
        self.random_state = random_state
        self.isFitted = False
        self.verbose = verbose
        self.n_components = n_components

    def _e_step(self, X, params):
        """ E-Step of the EM-algorithm.

        The E-step takes the existing parameters, for the components, bias
        and noise variance and computes sufficient statistics for the M-Step
        by taking the expectation of latent variables conditional on the
        visible variables. Also returns the likelihood for the data X and
        projections into latent space of the data.

        Args
        ----
        X : array, [nExamples, nFeatures]
            Matrix of training data, where nExamples is the number of
            examples and nFeatures is the number of features.
        W : array, [dataDim, latentDim]
            Component matrix data. Maps latent points to data space.
        b : array, [dataDim,]
            Data bias.
        sigmaSq : float
            Noise variance parameter.

        Returns
        -------
        ss : dict

        proj :

        ll :
        """
        # Get params
        mu_list = params['mu_list']
        components = params['components']
        n_examples, data_dim = X.shape
        
        # Compute responsibilities
        r = np.zeros([n_examples, self.n_components])
        
        # Get Sigma from params
        Sigma_list = self._params_to_Sigma(params)
        
        for k, mu, Sigma in zip(range(self.n_components), mu_list, Sigma_list):
#            r[:,k] = _mv_gaussian_pdf(X, mu, Sigma)
            r[:,k] = mvn.pdf(X, mu, Sigma)

        r = r * components
        r_sum = r.sum(axis=1)
        responsibilities = r / r_sum[:,np.newaxis]
            
        # Store sufficient statistics in dictionary
        ss = {
            'responsibilities' : responsibilities
             }
             
        # Compute log-likelihood of each example
        sample_ll = np.log(r_sum)
        
        return ss, sample_ll

    def _m_step(self, X, ss, params):
        """ M-Step of the EM-algorithm.

        The M-step takes the sufficient statistics computed in the E-step, and
        maximizes the expected complete data log-likelihood with respect to the
        parameters.

        Args
        ----
        ss : dict

        Returns
        -------
        params : dict

        """
        resp = ss['responsibilities']
        
        # Update components param
        components = np.mean(resp, axis=0)

        # Update mean / Sigma params
        mu_list = []
        Sigma_list = []        
        for r in resp.T:        
            mu = np.sum(X*r[:,np.newaxis], axis=0) / r.sum()
            mu_list.append(mu)          
            Sigma = (X*r[:,np.newaxis]).T.dot(X) / r.sum() - np.outer(mu, mu)
            Sigma_list.append(Sigma)

        # Store params in dictionary
        params = {
            'Sigma_list' : Sigma_list,
            'mu_list' : mu_list,
            'components' : components
             }

        return params
        
    def _params_to_Sigma(self, params):
        return params['Sigma_list']
        
    def _init_params(self, init_method, X=None):
        seed(self.random_state)
        n_examples = X.shape[0]
        if init_method == 'kmeans':
            kmeans = KMeans(self.n_components)
            kmeans.fit(X)
            mu_list = [k for k in kmeans.cluster_centers_]
            Sigma_list = [np.cov(X[kmeans.labels_==k,:].T) for k in range(self.n_components)]
            components = np.array([np.sum(kmeans.labels_==k) / n_examples for k in range(self.n_components)])
            params_init = {
                            'mu_list' : mu_list,
                            'Sigma_list' : Sigma_list,
                            'components' : components
                            }
            return params_init

    def fit(self, X, params_init=None, init_method='kmeans'):
        """ Fit the model using EM with data X.

        Args
        ----
        X : array, [nExamples, nFeatures]
            Matrix of training data, where nExamples is the number of
            examples and nFeatures is the number of features.
        """
        n_examples, data_dim = np.shape(X)
        self.data_dim = data_dim

        if params_init is None:
            params = self._init_params(init_method, X)
        else:
            params = params_init

        oldL = -np.inf
        for i in range(self.max_iter):

            # E-Step
            ss, sample_ll = self._e_step(X, params)

            # Evaluate likelihood
            ll = sample_ll.sum()
            if self.verbose:
                print("Iter {:d}   NLL: {:.3f}   Change: {:.3f}".format(i,
                      -ll, -(ll-oldL)), flush=True)

            # Break if change in likelihood is small
            if np.abs(ll - oldL) < self.tol:
                break
            oldL = ll

            # M-step
            params = self._m_step(X, ss, params)

        else:
            if self.verbose:
                print("PPCA did not converge within the specified tolerance." +
                      " You might want to increase the number of iterations.")

        # Update Object attributes
        self.params = params
        self.trainNll = ll
        self.isFitted = True

    def sample(self, n_samples=1):
        """Sample from fitted model.

        Sample from fitted model by first sampling from latent space
        (spherical Gaussian) then transforming into data space using learned
        parameters. Noise can then be added optionally.

        Parameters
        ----------
        nSamples : int
            Number of samples to generate
        noisy : bool
            Option to add noise to samples (default = True)

        Returns
        -------
        dataSamples : array [nSamples, dataDim]
            Collection of samples in data space.
        """
        if  not self.isFitted:
            print("Model is not yet fitted. First use fit to learn the model"
                   + " params.")
        else:
            components = self.params['components']
            mu_list = self.params['mu_list']
            Sigma_list = self._params_to_Sigma(self.params)
            components_cumsum = np.cumsum(components)
            samples = np.zeros([n_samples, self.data_dim])
            for n in range(n_samples):
                r = np.random.rand(1)
                z = np.argmin(r > components_cumsum)               
                samples[n] = rd.multivariate_normal(mu_list[z], Sigma_list[z])                
            return samples
            
    def score_samples(self, X):
        if not self.isFitted:
            print("Model is not yet fitted. First use fit to learn the model"
                   + " params.")
        else:
            # Apply one step of E-step to get the sample log-likelihoods
            return self._e_step(X, self.params)[1]

    def score(self, X):
        """Compute the average log-likelihood of data matrix X

        Parameters
        ----------
        X: array, shape (n_samples, n_features)
            The data

        Returns
        -------
        meanLl: array, shape (n_samples,)
            Log-likelihood of each sample under the current model
        """
        if not self.isFitted:
            print("Model is not yet fitted. First use fit to learn the model"
                   + " params.")
        else:
            # Apply one step of E-step to get the sample log-likelihoods
            sample_ll = self.score_samples(X)

            # Divide by number of examples to get average log likelihood
            return sample_ll.mean()
            
class SphericalGMM(GMM):
    
    def _init_params(self, init_method, X=None):
        seed(self.random_state)
        n_examples = X.shape[0]
        if init_method == 'kmeans':
            kmeans = KMeans(self.n_components)
            kmeans.fit(X)
            mu_list = [k for k in kmeans.cluster_centers_]
            sigma_sq_list = [np.mean(np.diag(np.cov(X[kmeans.labels_==k,:].T))) for k in range(self.n_components)]
            components = np.array([np.sum(kmeans.labels_==k) / n_examples for k in range(self.n_components)])
            params_init = {
                            'mu_list' : mu_list,
                            'sigma_sq_list' : sigma_sq_list,
                            'components' : components
                            }
            return params_init

    def _m_step(self, X, ss, params):
        """ M-Step of the EM-algorithm.

        The M-step takes the sufficient statistics computed in the E-step, and
        maximizes the expected complete data log-likelihood with respect to the
        parameters.

        Args
        ----
        ss : dict

        Returns
        -------
        params : dict

        """
        resp = ss['responsibilities']
#        resp_sum = 
        
        # Update components param
        components = np.mean(resp, axis=0)
        d = len(params['mu_list'][0])

        # Update mean / Sigma params
        mu_list = []
        sigma_sq_list = []      
        for r in resp.T:
            mu = np.sum(X*r[:,np.newaxis], axis=0) / r.sum()
            mu_list.append(mu)
            dev = X - mu                
            sigma_sq = np.trace(r[:,np.newaxis]*dev.dot(dev.T)) / (d*r.sum())
            sigma_sq_list.append(sigma_sq)
        # Store params in dictionary
        params = {
            'sigma_sq_list' : sigma_sq_list,
            'mu_list' : mu_list,
            'components' : components
             }

        return params
        
    def _params_to_Sigma(self, params):
            return [sigma_sq*np.eye(self.data_dim) for sigma_sq in 
                params['sigma_sq_list']]
        

class DiagonalGMM(GMM):
    
    def _init_params(self, init_method, X=None):
        seed(self.random_state)
        n_examples = X.shape[0]
        if init_method == 'kmeans':
            kmeans = KMeans(self.n_components)
            kmeans.fit(X)
            mu_list = [k for k in kmeans.cluster_centers_]
            Psi_list = [np.diag(np.diag(np.cov(X[kmeans.labels_==k,:].T))) for k in range(self.n_components)]
            components = np.array([np.sum(kmeans.labels_==k) / n_examples for k in range(self.n_components)])
            params_init = {
                            'mu_list' : mu_list,
                            'Psi_list' : Psi_list,
                            'components' : components
                            }
            return params_init
    
    def _m_step(self, X, ss, params):
        """ M-Step of the EM-algorithm.

        The M-step takes the sufficient statistics computed in the E-step, and
        maximizes the expected complete data log-likelihood with respect to the
        parameters.

        Args
        ----
        ss : dict

        Returns
        -------
        params : dict

        """
        resp = ss['responsibilities']
        
        # Update components param
        components = np.mean(resp, axis=0)

        # Update mean / Sigma params
        mu_list = []
        Psi_list = []        
        for r in resp.T:        
            mu = np.sum(X*r[:,np.newaxis], axis=0) / r.sum()
            mu_list.append(mu)          
            Psi = np.diag(np.diag((X*r[:,np.newaxis]).T.dot(X) / 
                r.sum() - np.outer(mu, mu)))
            Psi_list.append(Psi)

        # Store params in dictionary
        params = {
            'Psi_list' : Psi_list,
            'mu_list' : mu_list,
            'components' : components
             }

        return params
        
    def _params_to_Sigma(self, params):
            return params['Psi_list']
            
class MPPCA(GMM):
    
    def __init__(self, n_components, latent_dim, tol=1e-3, max_iter=1000, 
                  random_state=0, verbose=True):
        
        super(MPPCA,self).__init__(n_components, tol=1e-3, max_iter=1000, 
            random_state=0, verbose=True)
        self.latent_dim = latent_dim
    
    def _init_params(self, init_method, X=None):
        seed(self.random_state)
        n_examples = X.shape[0]
        if init_method == 'kmeans':
            kmeans = KMeans(self.n_components)
            kmeans.fit(X)
            mu_list = [k for k in kmeans.cluster_centers_]
            W_list = []
            sigma_sq_list = []
            for k in range(self.n_components):
                data_k = X[kmeans.labels_==k,:]
                pca = PCA(n_components=self.latent_dim)
                pca.fit(data_k)
                W_list.append(pca.components_.T)                
                sigma_sq_list.append(pca.noise_variance_)                
            components = np.array([np.sum(kmeans.labels_==k) / n_examples for k in range(self.n_components)])
            params_init = {
                            'mu_list' : mu_list,
                            'W_list' : W_list,
                            'sigma_sq_list' : sigma_sq_list,
                            'components' : components
                            }
            return params_init
            
    def _e_step(self, X, params):
        """ E-Step of the EM-algorithm.

        The E-step takes the existing parameters, for the components, bias
        and noise variance and computes sufficient statistics for the M-Step
        by taking the expectation of latent variables conditional on the
        visible variables. Also returns the likelihood for the data X and
        projections into latent space of the data.

        Args
        ----
        X : array, [nExamples, nFeatures]
            Matrix of training data, where nExamples is the number of
            examples and nFeatures is the number of features.
        W : array, [dataDim, latentDim]
            Component matrix data. Maps latent points to data space.
        b : array, [dataDim,]
            Data bias.
        sigmaSq : float
            Noise variance parameter.

        Returns
        -------
        ss : dict

        proj :

        ll :
        """
        # Get params
        mu_list = params['mu_list']
        components = params['components']
        W_list = params['W_list']
        sigma_sq_list = params['sigma_sq_list']
        n_examples, data_dim = X.shape
        
        # Compute responsibilities
        r = np.zeros([n_examples, self.n_components])
        
        # Get Sigma from params
        Sigma_list = self._params_to_Sigma(params)
        
        for k, mu, Sigma in zip(range(self.n_components), mu_list, Sigma_list):
            r[:,k] = _mv_gaussian_pdf(X, mu, Sigma)
        r = r * components
        r_sum = r.sum(axis=1)
        responsibilities = r / r_sum[:,np.newaxis]
        
        # Get sufficient statistics E[z] and E[zz^t] for each component
        z_list = []
        zz_list = []
        for mu, W, sigma_sq in zip(mu_list, W_list, sigma_sq_list):
            dev = X - mu
            F_inv = np.linalg.inv(W.T.dot(W) + sigma_sq*np.eye(self.latent_dim))
            z = dev.dot(W.dot(F_inv))
            z_list.append(z)
            zz = sigma_sq*F_inv + z[:,:,np.newaxis] * z[:,np.newaxis,:]            
            zz_list.append(zz)
            
        # Store sufficient statistics in dictionary
        ss = {
            'responsibilities' : responsibilities,
            'z_list' : z_list,
            'zz_list' : zz_list
             }
             
        # Compute log-likelihood
        sample_ll = np.log(r_sum)

        return ss, sample_ll
    
    def _m_step(self, X, ss, params):
        """ M-Step of the EM-algorithm.

        The M-step takes the sufficient statistics computed in the E-step, and
        maximizes the expected complete data log-likelihood with respect to the
        parameters.

        Args
        ----
        ss : dict

        Returns
        -------
        params : dict

        """
        resp = ss['responsibilities']
        z_list = ss['z_list']
        zz_list = ss['zz_list']
        W_list_old = params['W_list']        
        
        # Update components param
        components = np.mean(resp, axis=0)

        # Update mean / Sigma params
        mu_list = []
        W_list = []      
        sigma_sq_list = []
        for r, W, z, zz in zip(resp.T, W_list_old, z_list, zz_list):      
            # mu first
            resid = X - z.dot(W.T)
            mu = np.sum(resid*r[:,np.newaxis], axis=0) / r.sum()
            mu_list.append(mu)
            W1 = ((X-mu)*r[:,np.newaxis]).T.dot(z)
            W2 = np.linalg.inv(np.sum(zz*r[:,np.newaxis,np.newaxis], axis=0))
            W = W1.dot(W2)
            W_list.append(W)
            s1 = np.diag((X-mu).dot((X-mu).T))
            s2 = np.diag(-2*z.dot(W.T).dot((X-mu).T))
            s3 = np.trace(zz*W.T.dot(W)[np.newaxis,:,:], axis1=1, axis2=2)
            sigma_sq = np.sum(r*(s1 + s2 + s3)) / (self.data_dim * r.sum())
            sigma_sq_list.append(sigma_sq)

        # Store params in dictionary
        params = {
            'W_list' : W_list,
            'sigma_sq_list' : sigma_sq_list,
            'mu_list' : mu_list,
            'components' : components
             }

        return params
        
    def _params_to_Sigma(self, params):
        W_list = params['W_list']
        sigma_sq_list = params['sigma_sq_list']
        Sigma_list = [W.dot(W.T) + sigma_sq*np.eye(self.data_dim) 
            for W,sigma_sq in zip(W_list, sigma_sq_list)]
        return Sigma_list
        
class MFA(GMM):

    def __init__(self, n_components, latent_dim, tol=1e-3, max_iter=1000, 
                  random_state=0, verbose=True):
        
        super(MFA,self).__init__(n_components, tol=1e-3, max_iter=1000, 
            random_state=0, verbose=True)
        self.latent_dim = latent_dim
    
    def _init_params(self, init_method, X=None):
        seed(self.random_state)
        n_examples = X.shape[0]
        if init_method == 'kmeans':
            kmeans = KMeans(self.n_components)
            kmeans.fit(X)
            mu_list = [k for k in kmeans.cluster_centers_]
            W_list = []
            Psi_list = []
            for k in range(self.n_components):
                data_k = X[kmeans.labels_==k,:]
                fa = FactorAnalysis(n_components=self.latent_dim)
                fa.fit(data_k)
                W_list.append(fa.components_.T)                
                Psi_list.append(np.diag(fa.noise_variance_))                
            components = np.array([np.sum(kmeans.labels_==k) / n_examples for k in range(self.n_components)])
            params_init = {
                            'mu_list' : mu_list,
                            'W_list' : W_list,
                            'Psi_list' : Psi_list,
                            'components' : components
                            }
            return params_init
            
    def _e_step(self, X, params):
        """ E-Step of the EM-algorithm.

        The E-step takes the existing parameters, for the components, bias
        and noise variance and computes sufficient statistics for the M-Step
        by taking the expectation of latent variables conditional on the
        visible variables. Also returns the likelihood for the data X and
        projections into latent space of the data.

        Args
        ----
        X : array, [nExamples, nFeatures]
            Matrix of training data, where nExamples is the number of
            examples and nFeatures is the number of features.
        W : array, [dataDim, latentDim]
            Component matrix data. Maps latent points to data space.
        b : array, [dataDim,]
            Data bias.
        sigmaSq : float
            Noise variance parameter.

        Returns
        -------
        ss : dict

        proj :

        ll :
        """
        # Get params
        mu_list = params['mu_list']
        components = params['components']
        W_list = params['W_list']
        Psi_list = params['Psi_list']
        n_examples, data_dim = X.shape
        
        # Compute responsibilities
        r = np.zeros([n_examples, self.n_components])
        
        # Get Sigma from params
        Sigma_list = self._params_to_Sigma(params)
        
        for k, mu, Sigma in zip(range(self.n_components), mu_list, Sigma_list):
            r[:,k] = _mv_gaussian_pdf(X, mu, Sigma)
        r = r * components
        r_sum = r.sum(axis=1)
        responsibilities = r / r_sum[:,np.newaxis]
        
        # Get sufficient statistics E[z] and E[zz^t] for each component
        z_list = []
        zz_list = []
        for mu, W, Psi in zip(mu_list, W_list, Psi_list):
            dev = X - mu
            F_inv = np.linalg.inv(W.dot(W.T) + Psi)
            z = dev.dot(F_inv.dot(W))
            z_list.append(z)
            zz = (np.eye(self.latent_dim) - W.T.dot(F_inv).dot(W) 
                  + z[:,:,np.newaxis] * z[:,np.newaxis,:])          
            zz_list.append(zz)
            
        # Store sufficient statistics in dictionary
        ss = {
            'responsibilities' : responsibilities,
            'z_list' : z_list,
            'zz_list' : zz_list
             }
             
        # Compute log-likelihood
        sample_ll = np.log(r_sum)

        return ss, sample_ll
    
    def _m_step(self, X, ss, params):
        """ M-Step of the EM-algorithm.

        The M-step takes the sufficient statistics computed in the E-step, and
        maximizes the expected complete data log-likelihood with respect to the
        parameters.

        Args
        ----
        ss : dict

        Returns
        -------
        params : dict

        """
        resp = ss['responsibilities']
        z_list = ss['z_list']
        zz_list = ss['zz_list']
        W_list_old = params['W_list']      
        
        # Update components param
        components = np.mean(resp, axis=0)

        # Update mean / Sigma params
        mu_list = []
        W_list = []      
        Psi_list = []
        for r, W, z, zz in zip(resp.T, W_list_old, z_list, zz_list):      
            # mu 
            resid = X - z.dot(W.T)
            mu = np.sum(resid*r[:,np.newaxis], axis=0) / r.sum()
            mu_list.append(mu)
            
            # W
            W1 = ((X-mu)*r[:,np.newaxis]).T.dot(z) # Checked
            W2 = np.linalg.inv(np.sum(zz*r[:,np.newaxis,np.newaxis], axis=0))
            W = W1.dot(W2)
            W_list.append(W)
            
            # Psi
            s1 = ((X-mu)*r[:,np.newaxis]).T.dot(X-mu)
            s2 = W.dot((z*r[:,np.newaxis]).T.dot(X-mu))
            Psi = np.diag(np.diag(s1 - s2)) / r.sum() # Checked
            Psi_list.append(Psi)                            
                
        # Store params in dictionary
        params = {
            'W_list' : W_list,
            'Psi_list' : Psi_list,
            'mu_list' : mu_list,
            'components' : components
             }

        return params
        
    def _params_to_Sigma(self, params):
        W_list = params['W_list']
        Psi_list = params['Psi_list']
        Sigma_list = [W.dot(W.T) + Psi for W, Psi in zip(W_list, Psi_list)]
        return Sigma_list
