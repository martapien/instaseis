#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests for the syngine client. These tests might have to be adapted if the
service changes.

:copyright:
    Lion Krischer (krischer@geophysik.uni-muenchen.de), 2014
:license:
    GNU Lesser General Public License, Version 3 [non-commercial/academic use]
    (http://www.gnu.org/copyleft/lgpl.html)
"""
from __future__ import absolute_import

import copy
import numpy as np
import pytest

import instaseis
from .tornado_testing_fixtures import DBS

db_path = DBS["db_bwd_displ_only"]


@pytest.fixture(scope="module")
def syngine_client():
    return instaseis.open_db("syngine://test")


def test_info(syngine_client):
    """
    Make sure the /info route is similar enough.
    """
    # Syngine and local database.
    s_db = syngine_client
    l_db = instaseis.open_db(db_path)

    s_info = copy.deepcopy(s_db.info)
    l_info = copy.deepcopy(l_db.info)

    np.testing.assert_allclose(s_info.slip, l_info.slip)
    np.testing.assert_allclose(s_info.sliprate, l_info.sliprate)

    for key in ["directory", "slip", "sliprate"]:
        del s_info[key]
        del l_info[key]

    assert s_info.__dict__ == l_info.__dict__


def _compare_streams(s_db, l_db, kwargs):
    """
    Helper function comparing streams extracted from syngine and remote
    instaseis databases.
    """
    s_st = s_db.get_seismograms(**kwargs)
    l_st = l_db.get_seismograms(**kwargs)

    assert len(s_st) == len(l_st)

    for s_tr, l_tr in zip(s_st, l_st):
        # XXX: Delete the mu for now. Once implemented on the instaseis
        # server and forwarded by IRIS we will use it here.
        del s_tr.stats.instaseis
        del l_tr.stats.instaseis

        assert s_tr.stats.__dict__ == l_tr.stats.__dict__
        # Very small values have issues with floating point accuracy. 7
        # orders of magnitude should be more than accurate enough.

        np.testing.assert_allclose(s_tr.data, l_tr.data,
                                   atol=1E-6 * s_tr.data.ptp())


def test_seismogram_extraction(syngine_client):
    """
    Test the seismogram extraction from local and syngine databases.
    """
    # syngine and local database.
    s_db = syngine_client
    l_db = instaseis.open_db(db_path)

    source = instaseis.Source(
        latitude=4., longitude=3.0, depth_in_m=0, m_rr=4.71e+17, m_tt=3.81e+17,
        m_pp=-4.74e+17, m_rt=3.99e+17, m_rp=-8.05e+17, m_tp=-1.23e+17)

    receiver = instaseis.Receiver(latitude=10., longitude=20., depth_in_m=None)

    kwargs = {"source": source, "receiver": receiver,
              "components": ["Z", "N", "E", "R", "T"]}
    _compare_streams(s_db, l_db, kwargs)

    # Test velocity and acceleration.
    kwargs = {"source": source, "receiver": receiver,
              "components": ["Z", "N", "E", "R", "T"], "kind": "velocity"}
    _compare_streams(s_db, l_db, kwargs)
    kwargs = {"source": source, "receiver": receiver,
              "components": ["Z", "N", "E", "R", "T"], "kind": "acceleration"}
    _compare_streams(s_db, l_db, kwargs)

    # Test remove source shift.
    kwargs = {"source": source, "receiver": receiver,
              "components": ["Z", "N", "E", "R", "T"],
              "remove_source_shift": False}
    _compare_streams(s_db, l_db, kwargs)

    # Test resampling.
    kwargs = {"source": source, "receiver": receiver,
              "components": ["Z", "N", "E", "R", "T"],
              "dt": 1.0, "kernelwidth": 6}
    _compare_streams(s_db, l_db, kwargs)

    # Force sources currently raise an error.
    source = instaseis.ForceSource(
        latitude=89.91, longitude=0.0, depth_in_m=12000,
        f_r=1.23E10,
        f_t=2.55E10,
        f_p=1.73E10)
    kwargs = {"source": source, "receiver": receiver}
    with pytest.raises(ValueError) as e:
        _compare_streams(s_db, l_db, kwargs)

    assert e.value.args[0] == (
        "The Syngine Instaseis client does currently not "
        "support force sources. You can still download "
        "data from the Syngine service for force "
        "sources manually.")

    # Test less components and a latitude of 45 degrees to have the maximal
    # effect of geocentric vs geographic coordinates.
    source = instaseis.Source(
        latitude=45.0, longitude=3.0, depth_in_m=0, m_rr=4.71e+17,
        m_tt=3.81e+17, m_pp=-4.74e+17, m_rt=3.99e+17, m_rp=-8.05e+17,
        m_tp=-1.23e+17)

    receiver = instaseis.Receiver(latitude=-45.0, longitude=20.0,
                                  depth_in_m=None)
    kwargs = {"source": source, "receiver": receiver,
              "components": ["Z", "N", "E", "R", "T"]}
    _compare_streams(s_db, l_db, kwargs)


def test_str_method(syngine_client):
    str_repr = str(syngine_client)
    # Replace version number string to not be dependent on a certain version.
    str_repr = str_repr.replace("Syngine service version:  0.0.2", "XXX")

    assert str_repr.startswith(
        "SyngineInstaseisDB reciprocal Green's function Database (v7) "
        "generated with these parameters:\n"
        "Syngine model name:      'test'\n"
        "XXX\n"
        "\tcomponents           : vertical and horizontal\n"
        "\tvelocity model       : prem_iso_light\n"
        "\tattenuation          : False\n")