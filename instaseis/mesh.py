#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mesh object also taking care of opening and closing the netCDF files.

:copyright:
    Lion Krischer (krischer@geophysik.uni-muenchen.de), 2014
:license:
    GNU General Public License, Version 3
    (http://www.gnu.org/copyleft/gpl.html)
"""
from __future__ import absolute_import

import netCDF4
import numpy as np
from scipy.spatial import cKDTree
from collections import OrderedDict

from . import spectral_basis

MIN_FILE_VERSION = 4


class Buffer(object):
    """
    A simple buffer class based on a dictionary with a maximum memory. Last
    accessed items are removed last when the memory limit is hit.
    """
    def __init__(self, max_size_in_mb=100):
        self._max_size_in_bytes = max_size_in_mb * 1024**2
        self._total_size = 0
        self._buffer = OrderedDict()
        self._hits = 0
        self._fails = 0

    def __contains__(self, key):
        contains = key in self._buffer
        if contains:
            self._hits += 1
        else:
            self._fails += 1
        return contains

    def get(self, key):
        """
        Return an item from the buffer and move it to the end, so it is removed
        last.
        """
        value = self._buffer.pop(key)
        self._buffer[key] = value
        return value

    def add(self, key, value):
        """
        Add an item to the buffer and make sure that the buffer does not exceed
        the maximum size in memory.
        """
        self._buffer[key] = value
        # Assuming value is a numpy array
        self._total_size += value.nbytes

        # Remove existing values, until the size limit is fulfilled.
        while self._total_size > self._max_size_in_bytes:
            _, v = self._buffer.popitem(last=False)
            self._total_size -= v.nbytes

    def get_size_mb(self):
        return float(self._total_size) / 1024**2

    def efficiency(self):
        """
        Return the fraction of call to the __contains__() routine that returned
        True.
        """
        if (self._hits + self._fails) == 0:
            return 0.
        else:
            return float(self._hits) / float(self._hits + self._fails)


class Mesh(object):
    """
    A class to handle the netcdf output written by AxiSEM.
    """
    def __init__(self, filename, full_parse=False,
                 strain_buffer_size_in_mb=100, displ_buffer_size_in_mb=100,
                 read_on_demand=True):
        self.f = netCDF4.Dataset(filename, "r", format="NETCDF4")
        self.filename = filename
        self.read_on_demand = read_on_demand
        self._parse(full_parse=full_parse)
        self.strain_buffer = Buffer(strain_buffer_size_in_mb)
        self.displ_buffer = Buffer(displ_buffer_size_in_mb)

    def __del__(self):
        try:
            self.f.close()
        except:
            pass

    def _parse(self, full_parse=False):
        # Cheap sanity check. No need to parse the rest.
        self.dump_type = \
            getattr(self.f, "dump type (displ_only, displ_velo, fullfields)")
        if self.dump_type != "displ_only":
            raise NotImplementedError

        self.npol = self.f.npol

        try:
            self.file_version = getattr(self.f, "file version")
        except AttributeError:
            # very old files don't even have this attribute
            raise ValueError("Database file to old.")

        if self.file_version < MIN_FILE_VERSION:
            raise ValueError("Database file to old.")

        self.ndumps = getattr(self.f, "number of strain dumps")
        self.chunks = \
            self.f.groups["Snapshots"].variables.values()[0].chunking()
        self.excitation_type = getattr(self.f, "excitation type")

        # The rest is not needed for every mesh.

        if full_parse is False:
            return

        # Read some basic information to have easier access later on.
        self.source_type = getattr(self.f, "source type")
        self.amplitude = getattr(self.f, "scalar source magnitude")
        self.dt = getattr(self.f, "strain dump sampling rate in sec")
        self.source_shift = getattr(self.f, "source shift factor in sec")
        self.source_shift_samp = getattr(
            self.f, "source shift factor for deltat_coarse")

        self.stf_d = self.f.groups["Surface"].variables["stf_d_dump"][:]
        self.stf = self.f.groups["Surface"].variables["stf_dump"][:]

        self.stf_d_norm = self.stf_d / np.trapz(self.stf_d, dx=self.dt)

        self.npoints = self.f.npoints
        self.compression_level = \
            self.f.groups["Snapshots"].variables["disp_s"]\
            .filters()["complevel"]

        self.background_model = getattr(self.f, "background model")
        self.attenuation = bool(getattr(self.f, "attenuation"))
        self.planet_radius = getattr(self.f, "planet radius") * 1e3
        self.dominant_period = getattr(self.f, "dominant source period")
        self.axisem_version = getattr(self.f, "SVN revision")
        self.axisem_compiler = "%s %s" % (
            getattr(self.f, "compiler brand"),
            getattr(self.f, "compiler version"))
        self.axisem_user = "%s on %s" % (
            getattr(self.f, "user name"),
            getattr(self.f, "host name"))

        self.kwf_rmin = getattr(self.f, "kernel wavefield rmin")
        self.kwf_rmax = getattr(self.f, "kernel wavefield rmax")
        self.kwf_colatmin = getattr(self.f, "kernel wavefield colatmin")
        self.kwf_colatmax = getattr(self.f, "kernel wavefield colatmax")
        self.time_scheme = getattr(self.f, "time scheme")
        self.source_depth = getattr(self.f, "source depth in km")
        self.stf = getattr(self.f, "source time function")

        self.gll_points = spectral_basis.zelegl(self.npol)
        self.glj_points = spectral_basis.zemngl2(self.npol)
        self.G0, self.G1 = spectral_basis.def_lagrange_derivs_glj(self.npol)
        self.G2 = spectral_basis.def_lagrange_derivs_gll(self.npol)
        self.G1T = np.require(self.G1.transpose(),
                              requirements=["F_CONTIGUOUS"])
        self.G2T = np.require(self.G2.transpose(),
                              requirements=["F_CONTIGUOUS"])

        # Build a kdtree of the element midpoints.
        self.s_mp = self.f.groups["Mesh"].variables["mp_mesh_S"]
        self.z_mp = self.f.groups["Mesh"].variables["mp_mesh_Z"]

        self.mesh = np.empty((self.s_mp.shape[0], 2), dtype=self.s_mp.dtype)
        self.mesh[:, 0] = self.s_mp[:]
        self.mesh[:, 1] = self.z_mp[:]

        # Store some more index types in memory. While this increases memory
        # use it should be acceptable and result in much less netCDF reads.
        if not self.read_on_demand:
            self.fem_mesh = self.f.groups["Mesh"].variables["fem_mesh"][:]
            self.eltypes = self.f.groups["Mesh"].variables["eltype"][:]
            self.mesh_S = self.f.groups["Mesh"].variables["mesh_S"][:]
            self.mesh_Z = self.f.groups["Mesh"].variables["mesh_Z"][:]
            self.sem_mesh = self.f.groups["Mesh"].variables["sem_mesh"][:]
            self.axis = self.f.groups["Mesh"].variables["axis"][:]
            self.mesh_mu = self.f.groups["Mesh"].variables["mesh_mu"][:]

        self.kdtree = cKDTree(data=self.mesh)

    def get_n_closests_points(self, s, z, n=6):
        _, idx = self.kdtree.query([s, z], k=6)
        return self.mesh[idx]