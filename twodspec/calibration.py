# -*- coding: utf-8 -*-
"""

Author
------
Bo Zhang

Email
-----
bozhang@nao.cas.cn

Created on
----------
- Mon Nov 28 15:00:00 2016

Modifications
-------------
-

Aims
----
- wavelength calibration module
    all operations are based on extracted ThAr (1d)

"""

import itertools

import numpy as np
from astropy.io import fits
from joblib import Parallel, delayed
from scipy.interpolate import splrep, splev
from scipy.optimize import curve_fit, leastsq


# ############################## #
#      fix thar spectra
# ############################## #

def thar1d_fix(thar1d, conv_len=20, sat_count=50000):
    """ fix thar image, particularly for negative & saturated pixels

    Parameters
    ----------
    conv_len:
        convolved length
    sat_count:
        saturated count / max count

    Return
    ------
    thar1d_fixed:
        fixed THAR image

    """
    # ind_sat_conv
    ind_sat = thar1d >= sat_count
    ind_sat_conv = np.zeros_like(ind_sat)
    x_sat, y_sat = np.where(ind_sat)
    for i in range(len(x_sat)):
        ind_sat_conv[x_sat[i], y_sat[i] - conv_len:y_sat[i] + conv_len] = 1
    # ind_neg
    ind_neg = thar1d < 0.
    # combine to ind_bad
    ind_bad = np.logical_or(ind_neg, ind_sat_conv)
    thar1d_fixed = np.where(ind_bad, 0, thar1d)

    return thar1d_fixed


# ############################## #
#  2d correlation for 1d thar
# ############################## #

def thar1d_corr2d(thar1d_fixed, thar_temp, x_shiftmax=20, y_shiftmax=5,
                  verbose=False):
    """ determine the shift of *thar1d_fixed* relative to *thar_temp*

    Parameters
    ----------
    thar1d_fixed:
        image whose shift is to be determined
    thar_temp:
        reference image
    xtrim: tuple
        (xmin, xmax)
    ytrim: tuple
        (ymin, ymax)
    x_shiftmax: int
        the max y shift for correlation
    y_shiftmax: int
        the max y shift for correlation

    Returns
    -------
    (x_shift, y_shift), corr2d

    """

    # default trim region is the center 1/4 area
    xtrim = (thar1d_fixed.shape[1] * np.array([.2, .8])).astype(int)
    ytrim = (thar1d_fixed.shape[0] * np.array([.2, .8])).astype(int)

    # in case that the input data are int
    thar1d_fixed = np.array(thar1d_fixed).astype(float)
    thar_temp = np.array(thar_temp).astype(float)

    if verbose:
        print("@Cham: computing 2D cross-correlation...")

    # initialize result
    corr2d = np.zeros((1 + 2 * y_shiftmax, 1 + 2 * x_shiftmax))
    # make slice
    xslice_temp = slice(*xtrim)
    yslice_temp = slice(*ytrim)
    # 2D correlation
    for xofst in range(-x_shiftmax, x_shiftmax + 1, 1):
        for yofst in range(-y_shiftmax, y_shiftmax + 1, 1):
            xslice = slice(xtrim[0] + xofst, xtrim[1] + xofst, 1)
            yslice = slice(ytrim[0] + yofst, ytrim[1] + yofst, 1)
            corr2d[yofst + y_shiftmax, xofst + x_shiftmax] = \
                np.mean(thar1d_fixed[yslice, xslice] *
                        thar_temp[yslice_temp, xslice_temp])
    # select maximum value
    y_, x_ = np.where(corr2d == np.max(corr2d))
    x_shift = np.int(x_shiftmax - x_)  # reverse sign
    y_shift = np.int(y_shiftmax - y_)

    return (x_shift, y_shift), corr2d


# ############################## #
#      shift wave & order
# ############################## #

def interpolate_wavelength(w, shift, thar_temp, thar1d_fixed):
    """ interpolate given a regerence wavelength solution and shift """
    xshift, yshift = shift

    xcoord = np.arange(w.shape[1])
    ycoord = np.arange(w.shape[0])

    # for X, no difference, for Y, orders are different
    xcoord_xshift = xcoord + xshift
    ycoord_yshift = np.arange(
        w.shape[0] + thar1d_fixed.shape[0] - thar_temp.shape[0]) + yshift
    # shfit X
    w_x = np.zeros(w.shape)
    for i in range(w.shape[0]):
        s = splrep(xcoord, w[i], k=3, s=0)
        w_x[i] = splev(xcoord_xshift, s)

    # shift Y
    w_x_y = np.zeros((thar1d_fixed.shape[0], w.shape[1]))
    for j in range(w.shape[1]):
        s = splrep(ycoord, w_x[:, j], k=3, s=0)
        w_x_y[:, j] = splev(ycoord_yshift, s)

    return w_x_y


def interpolate_order(order_temp, shift, thar1d_fixed):
    xshift, yshift = shift

    # for X, no difference, for Y, orders are different
    ycoord_yshift = np.arange(thar1d_fixed.shape[0]) + \
                    order_temp[0, 0] + yshift
    order_interp = np.repeat(ycoord_yshift.reshape(-1, 1),
                             thar1d_fixed.shape[1], axis=1)

    return order_interp


# ############################## #
#      load temp thar spectra
# ############################## #

def load_thar_temp(thar_temp_path):
    hl = fits.open(thar_temp_path)
    wave = hl['wave'].data
    thar = hl['thar'].data
    order = hl['order'].data
    return wave, thar, order


# ############################## #
#      refine thar positions
# ############################## #

def refine_thar_positions(wave_init, order_init, thar1d_fixed, thar_list,
                          fit_width=5., lc_tol=5., k=3, n_jobs=10, verbose=10):
    """ refine ThAr positions """
    print("@TWODSPEC: refine ThAr positions ...")

    # refine thar positions for each order
    r = Parallel(n_jobs=n_jobs, verbose=verbose, batch_size=1)(
        delayed(refine_thar_positions_order)(
            wave_init[i_order],
            np.arange(wave_init.shape[1]),
            thar1d_fixed[i_order],
            thar_list[(thar_list > np.min(wave_init[i_order]) + 1.) * (
                thar_list < np.max(wave_init[i_order]) - 1.)],
            order_init[i_order, 0],
            fit_width=fit_width, lc_tol=lc_tol, k=k
        ) for i_order in range(wave_init.shape[0]))

    # remove all null values
    null_value = None
    for i in range(r.count(null_value)):
        r.remove(null_value)
        # print(len(r))

    # collect data
    lc_coord = np.array(np.hstack([_[0] for _ in r]))
    lc_order = np.array(np.hstack([_[1] for _ in r]))
    lc_thar = np.array(np.hstack([_[2] for _ in r]))
    popt = np.array(np.vstack([_[3] for _ in r]))
    pcov = np.array(np.vstack([_[4] for _ in r]))

    return lc_coord, lc_order, lc_thar, popt, pcov


def refine_thar_positions_order(this_wave_init, this_xcoord, this_thar,
                                this_thar_list, this_order, fit_width=5.,
                                lc_tol=5., k=3):
    if len(this_thar_list) == 0:
        return None

    popt_list = []
    pcov_list = []

    # refine all thar positions in this order
    for i_thar_line, each_thar_line in enumerate(this_thar_list):

        # cut local spectrum
        ind_local = (this_wave_init > each_thar_line - fit_width) * (
            this_wave_init < each_thar_line + fit_width)

        # set bounds
        p0 = (0., 1E5, each_thar_line, 0.1)
        bounds = ((-1., 0., each_thar_line - lc_tol, 0.01),
                  (+np.inf, np.inf, each_thar_line + lc_tol, 2.))

        try:
            popt, pcov = curve_fit(gauss_poly0, this_wave_init[ind_local],
                                   this_thar[ind_local], p0=p0, bounds=bounds)
            pcov = np.diagonal(pcov)
        except RuntimeError:
            popt = np.ones_like(p0) * np.nan
            pcov = np.ones((len(p0),)) * np.nan

        popt_list.append(popt)
        pcov_list.append(pcov)

    # interpolation for X corrdinates
    if np.all(np.diff(this_wave_init) >= 0):
        tck = splrep(this_wave_init, this_xcoord, k=k)
        # print(this_wave_init, this_xcoord, k)
    elif np.all(np.diff(this_wave_init) <= 0):
        tck = splrep(this_wave_init[::-1], this_xcoord[::-1], k=k)
        print("@SONG: wavelength inversed!")
    else:
        raise (ValueError("@Cham: error occurs in interpolation!"))

    # lccov_list = np.array(lccov_list)
    popt_list = np.array(popt_list)
    pcov_list = np.array(pcov_list)

    lc_coord = splev(popt_list[:, 2], tck)
    lc_order = np.ones_like(lc_coord) * this_order

    return lc_coord, lc_order, this_thar_list, popt_list, pcov_list


# ############################## #
#      2D surface fit
# ############################## #

def polyval2d(x, y, coefs, orders=None):
    if orders is None:
        orderx, ordery = coefs.shape
    else:
        orderx, ordery = orders

    ij = itertools.product(range(orderx + 1), range(ordery + 1))
    z = np.zeros_like(x)
    for a, (i, j) in zip(coefs.flatten(), ij):
        if i + j < np.max((orderx, ordery)):
            #        print a,i,j
            z += a * x ** i * y ** j
    return z


def gauss_poly1(x, p0, p1, a, b, c):
    return p0 + p1 * x + a/np.sqrt(2.*np.pi)/c * np.exp(-0.5*((x - b) / c) ** 2.)


def gauss_poly0(x, p0, a, b, c):
    return p0 + a/np.sqrt(2.*np.pi)/c * np.exp(-0.5 * ((x - b) / c) ** 2.)


def gauss(x, a, b, c):
    return a/np.sqrt(2.*np.pi)/c * np.exp(-0.5 * ((x - b) / c) ** 2.)


def residual_chi2(coefs, x, y, z, w, poly_order):
    return np.nansum(residual(coefs, x, y, z, w, poly_order) ** 2.)


def residual_lar(coefs, x, y, z, w, poly_order):
    fitted = polyval2d(x, y, coefs, poly_order)
    return np.sqrt(np.abs((fitted - z) * w))


def residual(coefs, x, y, z, w, poly_order):
    fitted = polyval2d(x, y, coefs, poly_order)
    return (fitted - z) * w


# ############################## #
#      standardization
# ############################## #

def standardize_inverse(x, xmean, xstd):
    return (x * xstd) + xmean


def standardize(x):
    return (x - np.nanmean(x)) / np.nanstd(x), np.nanmean(x), np.nanstd(x)


# ############################## #
#      fit grating equation
# ############################## #

def fit_grating_equation(lc_coord, lc_order, lc_thar, popt, pcov, ind_good_thar0=None,
                         poly_order=(3, 5), max_dev_threshold=100, n_iter=400, lar=False,
                         nl_eachorder=10):
    # pick good thar lines
    if ind_good_thar0 is None:
        ind_good_thar0 = np.ones_like(lc_coord, dtype=bool)
    try:
        ind_good_thar = ind_good_thar0 * np.isfinite(lc_coord) * \
                        (popt[:, 3] < 1.0) * (pcov[:, 2] < 1.0)
    except:
        ind_good_thar = np.ones_like(lc_coord, dtype=bool)

    lc_coord = lc_coord[ind_good_thar]
    lc_order = lc_order[ind_good_thar]
    lc_thar = lc_thar[ind_good_thar]

    # standardization
    lc_coord_s, lc_coord_mean, lc_coord_std = standardize(lc_coord)
    lc_order_s, lc_order_mean, lc_order_std = standardize(lc_order)
    ml_s, ml_mean, ml_std = standardize(lc_thar * lc_order)
    # scaler
    scaler_coord = lc_coord_mean, lc_coord_std
    scaler_order = lc_order_mean, lc_order_std
    scaler_ml = ml_mean, ml_std

    # weight
    weight = popt[ind_good_thar, 1] / popt[ind_good_thar, 0]
    weight = weight > 0

    # fit surface
    x0 = np.zeros(poly_order)
    #print(lc_coord_s, lc_order_s, ml_s, weight, poly_order)
    x0, ier = leastsq(residual, x0,
                      args=(lc_coord_s, lc_order_s, ml_s, weight, poly_order))
    #print(x0, ier)

    # iter
    if n_iter > 0:
        n_loop = 0
        while n_loop < n_iter:
            n_loop += 1
            if lar:
                x_mini_lsq, ier = leastsq(residual_lar, x0, args=(
                    lc_coord_s, lc_order_s, ml_s, weight, poly_order))
            else:
                x_mini_lsq, ier = leastsq(residual, x0, args=(
                    lc_coord_s, lc_order_s, ml_s, weight, poly_order))
            fitted = polyval2d(lc_coord_s, lc_order_s, x_mini_lsq, poly_order)
            fitted_wave = standardize_inverse(fitted, ml_mean, ml_std) / lc_order
            fitted_wave_diff = fitted_wave - lc_thar

            wave_dev = np.abs(fitted_wave_diff - np.nanmedian(fitted_wave_diff))
            lc_order_s_unique, ind_inverse = np.unique(lc_order_s, return_inverse=True)
            lc_order_s_unique_left = np.zeros_like(lc_order_s_unique, dtype=int)
            for i, _ in enumerate(lc_order_s_unique):
                lc_order_s_unique_left[i] = np.sum(weight[lc_order_s == _] > 0)
            lc_order_s_left = lc_order_s_unique_left[ind_inverse]
            # print(lc_order_s_left)
            # print(wave_dev)
            # print(lc_order_s_left)

            possible_outlier = wave_dev * (wave_dev > max_dev_threshold) * (lc_order_s_left > nl_eachorder)
            if np.any(possible_outlier > 0):
                ind_max_dev = np.nanargmax(possible_outlier)
                weight[ind_max_dev] = 0.
                ml_s[ind_max_dev] = np.nan
                lc_thar[ind_max_dev] = np.nan
                print("@Cham: [n_loop = %s] max_dev = %s" % (
                    n_loop, fitted_wave_diff[ind_max_dev]))
            else:
                if n_loop == 0:
                    print("@Cham: no points cut in iterarions ...")
                break
    else:
        if lar:
            x_mini_lsq, ier = leastsq(residual_lar, x0, args=(
                lc_coord_s, lc_order_s, ml_s, weight, poly_order))
        else:
            x_mini_lsq, ier = leastsq(residual, x0, args=(
                lc_coord_s, lc_order_s, ml_s, weight, poly_order))
        fitted = polyval2d(lc_coord_s, lc_order_s, x_mini_lsq, poly_order)
        fitted_wave = standardize_inverse(fitted, ml_mean, ml_std) / lc_order
        fitted_wave_diff = fitted_wave - lc_thar

        ind_kick = np.abs(fitted_wave_diff - np.nanmedian(fitted_wave_diff)) > max_dev_threshold
        weight[ind_kick] = 0.
        ml_s[ind_kick] = np.nan
        lc_thar[ind_kick] = np.nan
        x_mini_lsq, ier = leastsq(residual_lar, x0, args=(
            lc_coord_s, lc_order_s, ml_s, weight, poly_order))

    ind_good_thar = np.where(ind_good_thar)[0][np.isfinite(lc_thar)]
    print("@SONG: RMS = {0} | n_points = {1} ".format(
        np.sqrt(np.nanmean(np.square(fitted_wave - lc_thar))),
        np.sum(np.isfinite(lc_thar)))
    )

    return x_mini_lsq, ind_good_thar, scaler_coord, scaler_order, scaler_ml


def grating_equation_predict(grid_coord, grid_order, x_mini_lsq, poly_order,
                             scaler_coord, scaler_order, scaler_ml):
    sgrid_coord = (grid_coord - scaler_coord[0]) / scaler_coord[1]
    sgrid_order = (grid_order - scaler_order[0]) / scaler_order[1]

    sgrid_fitted = polyval2d(sgrid_coord.flatten(), sgrid_order.flatten(),
                             x_mini_lsq, poly_order)
    sgrid_fitted_wave = (sgrid_fitted * scaler_ml[1] + scaler_ml[0]) / \
                        grid_order.flatten()
    sgrid_fitted_wave = sgrid_fitted_wave.reshape(grid_coord.shape)
    return sgrid_fitted_wave


def polyfit_reject(x, y, deg=1, w=None, epsilon=0.002, n_reserve=-1):

    x, y = np.array(x), np.array(y)
    ind_good = np.ones_like(x, bool)
    x_, y_ = x[ind_good], y[ind_good]

    if w is None:
        w = np.ones_like(x, float)
    w_ = np.array(w)[ind_good]

    n_good = np.sum(ind_good)
    if isinstance(n_reserve, float):
        # fraction
        assert 0 < n_reserve < 1
        n_reserve = np.int(n_good * n_reserve)

    # set up the iteration
    ind_reserved = np.ones_like(x_, bool)
    while np.sum(ind_reserved) > n_reserve:
        # p0 = np.polyfit(x_[ind_reserved], y_[ind_reserved], deg, w=w_[ind_reserved])
        p0, ier = leastsq(polyfit_costfun_lar, np.zeros((deg+1,), float), args=(x_[ind_reserved], y_[ind_reserved], w_[ind_reserved]))

        y_res = y_ - np.polyval(p0, x_)
        y_res = np.where(w_ > 0, y_res, np.nan)
        # y_res_std = np.nanstd(y_res)
        # y_res_med = np.nanmedian(y_res)
        # y_res_scaled = (y_res>0) * (y_res/y_res_std/sigma_reject[0]) + (y_res<=0) * (y_res/y_res_std/sigma_reject[1])
        ind_bad = np.abs(y_res) > epsilon

        if np.any(ind_bad[ind_reserved]):
            # print(np.sum(ind_bad[ind_reserved]))
            i_rejected = np.nanargmax(np.abs(y_res))
            # print(i_rejected, np.abs(y_res)[i_rejected])
            ind_reserved[i_rejected] = False
            w_[i_rejected] = 0
            # print("@SONG: {0} points left".format(np.sum(ind_reserved)))
        else:
            break
    p = np.polyfit(x_[ind_reserved], y_[ind_reserved], deg, w=w_[ind_reserved])
    return p, w_>0


def polyfit_costfun_lar(p, x, y, w):
    return np.sqrt(np.abs(np.polyval(p, x) - y)) * w


def polyfit_costfun(p, x, y, w):
    return (np.polyval(p, x) - y) * w


def clean_thar_polyfit1d_reject(lc_coord, lc_order, lc_thar, popt,
                                ind_good0=None, deg=1, w=None,
                                epsilon=0.004, n_reserve=3):
    if ind_good0 is not None:
        lc_coord[~ind_good0] = np.nan
    print("@TWODSPEC: number of finite line centers: {}".format(
        np.sum(np.isfinite(lc_coord))))

    sub_good = np.zeros((0,), int)
    for i_order in np.unique(lc_order)[:]:
        this_ind = lc_order == i_order
        this_sub = np.where(this_ind)[0]
        this_lc_coord = lc_coord[this_ind]
        # this_lc_order = lc_order[this_ind]
        this_lc_thar = lc_thar[this_ind]

        x, y, z = this_lc_coord, popt[this_ind, 2] - this_lc_thar, this_lc_thar
        ind_use = np.isfinite(x) * np.isfinite(y)
        x, y, z = x[ind_use], y[ind_use], z[ind_use]

        p, ind_reserved = polyfit_reject(x, y, deg, w, epsilon=epsilon, n_reserve=n_reserve)
        sub_good = np.hstack((sub_good, this_sub[ind_use][ind_reserved]))
        # print(len(this_sub[ind_use][ind_reserved]))

    ind_good = np.zeros_like(lc_coord, bool)
    ind_good[sub_good] = True
    return ind_good