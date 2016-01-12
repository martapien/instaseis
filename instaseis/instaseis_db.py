#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Python library to extract seismograms from a set of wavefields generated by
AxiSEM.

:copyright:
    Martin van Driel (Martin@vanDriel.de), 2014
    Lion Krischer (krischer@geophysik.uni-muenchen.de), 2014
:license:
    GNU Lesser General Public License, Version 3 [non-commercial/academic use]
    (http://www.gnu.org/copyleft/lgpl.html)
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import collections
import numpy as np
from obspy.signal.util import nextpow2
import os

from . import InstaseisError, InstaseisNotFoundError
from .base_instaseis_db import BaseInstaseisDB
from . import finite_elem_mapping
from . import mesh
from . import rotations
from . import sem_derivatives
from . import spectral_basis
from .source import Source, ForceSource


MeshCollection_bwd = collections.namedtuple("MeshCollection_bwd", ["px", "pz"])
MeshCollection_fwd = collections.namedtuple("MeshCollection_fwd", ["m1", "m2",
                                                                   "m3", "m4"])


class InstaseisDB(BaseInstaseisDB):
    """
    Class for extracting seismograms from a local Instaseis database.
    """
    def __init__(self, db_path, buffer_size_in_mb=100, read_on_demand=False,
                 *args, **kwargs):
        """
        :param db_path: Path to the Instaseis Database containing
            subdirectories PZ and/or PX each containing a
            ``order_output.nc4`` file.
        :type db_path: str
        :param buffer_size_in_mb: Strain and displacement are buffered to
            avoid repeated disc access. Depending on the type of database
            and the number of components of the database, the total buffer
            memory can be up to four times this number. The optimal value is
            highly application and system dependent.
        :type buffer_size_in_mb: int, optional
        :param read_on_demand: Read several global fields on demand (faster
            initialization) or on initialization (slower
            initialization, faster in individual seismogram extraction,
            useful e.g. for finite sources, default).
        :type read_on_demand: bool, optional
        """

        self.db_path = db_path
        self.buffer_size_in_mb = buffer_size_in_mb
        self.read_on_demand = read_on_demand
        self._find_and_open_files()

    def _find_and_open_files(self):
        """
        Helper function walking the file tree below self.db_path and
        attempts to find the correct netCDF files.
        """
        found_files = []
        for root, dirs, filenames in os.walk(self.db_path, followlinks=True):
            # Limit depth of filetree traversal
            nested_levels = os.path.relpath(root, self.db_path).split(
                os.path.sep)
            if len(nested_levels) >= 4:
                del dirs[:]
            if "ordered_output.nc4" not in filenames:
                continue
            found_files.append(os.path.join(root, "ordered_output.nc4"))

        if len(found_files) == 0:
            raise InstaseisNotFoundError(
                "No suitable netCDF files found under '%s'" % self.db_path)
        elif len(found_files) not in [1, 2, 4]:
            raise InstaseisError(
                "1, 2 or 4 netCDF must be present in the folder structure. "
                "Found %i: \t%s" % (len(found_files),
                                    "\n\t".join(found_files)))

        # Parse to find the correct components.
        netcdf_files = collections.defaultdict(list)
        patterns = ["PX", "PZ", "MZZ", "MXX_P_MYY", "MXZ_MYZ", "MXY_MXX_M_MYY"]
        for filename in found_files:
            s = os.path.relpath(filename, self.db_path).split(os.path.sep)
            for p in patterns:
                if p in s:
                    netcdf_files[p].append(filename)

        # Assert at most one file per type.
        for key, files in netcdf_files.items():
            if len(files) != 1:
                raise InstaseisError(
                    "Found %i files for component %s:\n\t%s" % (
                        len(files), key, "\n\t".join(files)))
            netcdf_files[key] = files[0]

        # Two valid cases.
        if "PX" in netcdf_files or "PZ" in netcdf_files:
            self._parse_fs_meshes(netcdf_files)
        elif "MZZ" in netcdf_files or "MXX_P_MYY" in netcdf_files or \
                "MXZ_MYZ" in netcdf_files or "MXY_MXX_M_MYY" in netcdf_files:
            if sorted(netcdf_files.keys()) != sorted([
                    "MZZ", "MXX_P_MYY", "MXZ_MYZ", "MXY_MXX_M_MYY"]):
                raise InstaseisError(
                    "Expecting all four elemental moment tensor subfolders "
                    "to be present.")
            self._parse_mt_meshes(netcdf_files)
        else:
            raise InstaseisError(
                "Could not find any suitable netCDF files. Did you pass the "
                "correct directory? E.g. if the 'ordered_output.nc4' files "
                "are located in '/path/to/PZ/Data', please pass '/path/to/' "
                "to Instaseis.")

    def _parse_fs_meshes(self, files):
        if "PX" in files:
            px_file = files["PX"]
            x_exists = True
        else:
            x_exists = False
        if "PZ" in files:
            pz_file = files["PZ"]
            z_exists = True
        else:
            z_exists = False

        # full_parse will force the kd-tree to be built
        if x_exists and z_exists:
            px_m = mesh.Mesh(
                px_file, full_parse=True,
                strain_buffer_size_in_mb=self.buffer_size_in_mb,
                displ_buffer_size_in_mb=self.buffer_size_in_mb,
                read_on_demand=self.read_on_demand)
            pz_m = mesh.Mesh(
                pz_file, full_parse=False,
                strain_buffer_size_in_mb=self.buffer_size_in_mb,
                displ_buffer_size_in_mb=self.buffer_size_in_mb,
                read_on_demand=self.read_on_demand)
            self.parsed_mesh = px_m
        elif x_exists:
            px_m = mesh.Mesh(
                px_file, full_parse=True,
                strain_buffer_size_in_mb=self.buffer_size_in_mb,
                displ_buffer_size_in_mb=self.buffer_size_in_mb,
                read_on_demand=self.read_on_demand)
            pz_m = None
            self.parsed_mesh = px_m
        elif z_exists:
            px_m = None
            pz_m = mesh.Mesh(
                pz_file, full_parse=True,
                strain_buffer_size_in_mb=self.buffer_size_in_mb,
                displ_buffer_size_in_mb=self.buffer_size_in_mb,
                read_on_demand=self.read_on_demand)
            self.parsed_mesh = pz_m
        else:
            # Should not happen.
            raise NotImplementedError
        self.meshes = MeshCollection_bwd(px=px_m, pz=pz_m)
        self._is_reciprocal = True

    def _parse_mt_meshes(self, files):
        m1_m = mesh.Mesh(
            files["MZZ"], full_parse=True, strain_buffer_size_in_mb=0,
            displ_buffer_size_in_mb=self.buffer_size_in_mb,
            read_on_demand=self.read_on_demand)
        m2_m = mesh.Mesh(
            files["MXX_P_MYY"], full_parse=False, strain_buffer_size_in_mb=0,
            displ_buffer_size_in_mb=self.buffer_size_in_mb,
            read_on_demand=self.read_on_demand)
        m3_m = mesh.Mesh(
            files["MXZ_MYZ"], full_parse=False, strain_buffer_size_in_mb=0,
            displ_buffer_size_in_mb=self.buffer_size_in_mb,
            read_on_demand=self.read_on_demand)
        m4_m = mesh.Mesh(
            files["MXY_MXX_M_MYY"], full_parse=False,
            strain_buffer_size_in_mb=0,
            displ_buffer_size_in_mb=self.buffer_size_in_mb,
            read_on_demand=self.read_on_demand)
        self.parsed_mesh = m1_m

        self.meshes = MeshCollection_fwd(m1_m, m2_m, m3_m, m4_m)
        self._is_reciprocal = False

    def _get_seismograms(self, source, receiver, components=("Z", "N", "E")):
        """
        Extract seismograms for a moment tensor point source from the AxiSEM
        database.

        :param source: instaseis.Source or instaseis.ForceSource object
        :type source: :class:`instaseis.source.Source` or
            :class:`instaseis.source.ForceSource`
        :param receiver: instaseis.Receiver object
        :type receiver: :class:`instaseis.source.Receiver`
        :param components: a tuple containing any combination of the
            strings ``"Z"``, ``"N"``, ``"E"``, ``"R"``, and ``"T"``
        """
        if self.info.is_reciprocal:
            a, b = source, receiver
        else:
            a, b = receiver, source
        rotmesh_s, rotmesh_phi, rotmesh_z = rotations.rotate_frame_rd(
            a.x(planet_radius=self.info.planet_radius),
            a.y(planet_radius=self.info.planet_radius),
            a.z(planet_radius=self.info.planet_radius),
            b.longitude, b.colatitude)

        k_map = {"displ_only": 6,
                 "strain_only": 1,
                 "fullfields": 1}

        nextpoints = self.parsed_mesh.kdtree.query(
            [rotmesh_s, rotmesh_z], k=k_map[self.info.dump_type])

        # Find the element containing the point of interest.
        mesh = self.parsed_mesh.f["Mesh"]
        if self.info.dump_type == 'displ_only':
            for idx in nextpoints[1]:
                corner_points = np.empty((4, 2), dtype="float64")

                if not self.read_on_demand:
                    corner_point_ids = self.parsed_mesh.fem_mesh[idx][:4]
                    eltype = self.parsed_mesh.eltypes[idx]
                    corner_points[:, 0] = \
                        self.parsed_mesh.mesh_S[corner_point_ids]
                    corner_points[:, 1] = \
                        self.parsed_mesh.mesh_Z[corner_point_ids]
                else:
                    corner_point_ids = mesh["fem_mesh"][idx][:4]

                    # When reading from a netcdf file, the indices must be
                    # sorted for newer netcdf versions. The double argsort()
                    # gives the indices in the sorted array to restore the
                    # original order.
                    order = corner_point_ids.argsort().argsort()
                    corner_point_ids.sort()

                    eltype = mesh["eltype"][idx]
                    corner_points[:, 0] = \
                        mesh["mesh_S"][corner_point_ids][order]
                    corner_points[:, 1] = \
                        mesh["mesh_Z"][corner_point_ids][order]

                isin, xi, eta = finite_elem_mapping.inside_element(
                    rotmesh_s, rotmesh_z, corner_points, eltype,
                    tolerance=1E-3)
                if isin:
                    id_elem = idx
                    break
            else:
                raise ValueError("Element not found")

            if not self.read_on_demand:
                gll_point_ids = self.parsed_mesh.sem_mesh[id_elem]
                axis = bool(self.parsed_mesh.axis[id_elem])
            else:
                gll_point_ids = mesh["sem_mesh"][id_elem]
                axis = bool(mesh["axis"][id_elem])

            if axis:
                col_points_xi = self.parsed_mesh.glj_points
                col_points_eta = self.parsed_mesh.gll_points
            else:
                col_points_xi = self.parsed_mesh.gll_points
                col_points_eta = self.parsed_mesh.gll_points
        else:
            id_elem = nextpoints[1]

        # Collect data arrays and mu in a dictionary.
        data = {}

        # Get mu.
        if not self.read_on_demand:
            mesh_mu = self.parsed_mesh.mesh_mu
        else:
            mesh_mu = mesh["mesh_mu"]
        if self.info.dump_type == "displ_only":
            npol = self.info.spatial_order
            mu = mesh_mu[gll_point_ids[npol // 2, npol // 2]]
        else:
            # XXX: Is this correct?
            mu = mesh_mu[id_elem]
        data["mu"] = mu

        if self.info.is_reciprocal:

            fac_1_map = {"N": np.cos,
                         "E": np.sin}
            fac_2_map = {"N": lambda x: - np.sin(x),
                         "E": np.cos}

            if isinstance(source, Source):
                if self.info.dump_type == 'displ_only':
                    if axis:
                        G = self.parsed_mesh.G2
                        GT = self.parsed_mesh.G1T
                    else:
                        G = self.parsed_mesh.G2
                        GT = self.parsed_mesh.G2T

                strain_x = None
                strain_z = None

                # Minor optimization: Only read if actually requested.
                if "Z" in components:
                    if self.info.dump_type == 'displ_only':
                        strain_z = self.__get_strain_interp(
                            self.meshes.pz, id_elem, gll_point_ids, G, GT,
                            col_points_xi, col_points_eta, corner_points,
                            eltype, axis, xi, eta)
                    elif (self.info.dump_type == 'fullfields' or
                            self.info.dump_type == 'strain_only'):
                        strain_z = self.__get_strain(self.meshes.pz, id_elem)

                if any(comp in components for comp in ['N', 'E', 'R', 'T']):
                    if self.info.dump_type == 'displ_only':
                        strain_x = self.__get_strain_interp(
                            self.meshes.px, id_elem, gll_point_ids, G, GT,
                            col_points_xi, col_points_eta, corner_points,
                            eltype, axis, xi, eta)
                    elif (self.info.dump_type == 'fullfields' or
                          self.info.dump_type == 'strain_only'):
                        strain_x = self.__get_strain(self.meshes.px, id_elem)

                mij = rotations\
                    .rotate_symm_tensor_voigt_xyz_src_to_xyz_earth(
                        source.tensor_voigt, np.deg2rad(source.longitude),
                        np.deg2rad(source.colatitude))
                mij = rotations\
                    .rotate_symm_tensor_voigt_xyz_earth_to_xyz_src(
                        mij, np.deg2rad(receiver.longitude),
                        np.deg2rad(receiver.colatitude))
                mij = rotations.rotate_symm_tensor_voigt_xyz_to_src(
                    mij, rotmesh_phi)
                mij /= self.parsed_mesh.amplitude

                if "Z" in components:
                    final = np.zeros(strain_z.shape[0], dtype="float64")
                    for i in range(3):
                        final += mij[i] * strain_z[:, i]
                    final += 2.0 * mij[4] * strain_z[:, 4]
                    data["Z"] = final

                if "R" in components:
                    final = np.zeros(strain_x.shape[0], dtype="float64")
                    final -= strain_x[:, 0] * mij[0] * 1.0
                    final -= strain_x[:, 1] * mij[1] * 1.0
                    final -= strain_x[:, 2] * mij[2] * 1.0
                    final -= strain_x[:, 4] * mij[4] * 2.0
                    data["R"] = final

                if "T" in components:
                    final = np.zeros(strain_x.shape[0], dtype="float64")
                    final += strain_x[:, 3] * mij[3] * 2.0
                    final += strain_x[:, 5] * mij[5] * 2.0
                    data["T"] = final

                for comp in ["E", "N"]:
                    if comp not in components:
                        continue

                    fac_1 = fac_1_map[comp](rotmesh_phi)
                    fac_2 = fac_2_map[comp](rotmesh_phi)

                    final = np.zeros(strain_x.shape[0], dtype="float64")
                    final += strain_x[:, 0] * mij[0] * 1.0 * fac_1
                    final += strain_x[:, 1] * mij[1] * 1.0 * fac_1
                    final += strain_x[:, 2] * mij[2] * 1.0 * fac_1
                    final += strain_x[:, 3] * mij[3] * 2.0 * fac_2
                    final += strain_x[:, 4] * mij[4] * 2.0 * fac_1
                    final += strain_x[:, 5] * mij[5] * 2.0 * fac_2
                    if comp == "N":
                        final *= -1.0
                    data[comp] = final

            elif isinstance(source, ForceSource):
                if self.info.dump_type != 'displ_only':
                    raise ValueError("Force sources only in displ_only mode")

                if "Z" in components:
                    displ_z = self.__get_displacement(self.meshes.pz, id_elem,
                                                      gll_point_ids,
                                                      col_points_xi,
                                                      col_points_eta, xi, eta)

                if any(comp in components for comp in ['N', 'E', 'R', 'T']):
                    displ_x = self.__get_displacement(self.meshes.px, id_elem,
                                                      gll_point_ids,
                                                      col_points_xi,
                                                      col_points_eta, xi, eta)

                force = rotations.rotate_vector_xyz_src_to_xyz_earth(
                    source.force_tpr, np.deg2rad(source.longitude),
                    np.deg2rad(source.colatitude))
                force = rotations.rotate_vector_xyz_earth_to_xyz_src(
                    force, np.deg2rad(receiver.longitude),
                    np.deg2rad(receiver.colatitude))
                force = rotations.rotate_vector_xyz_to_src(
                    force, rotmesh_phi)
                force /= self.parsed_mesh.amplitude

                if "Z" in components:
                    final = np.zeros(displ_z.shape[0], dtype="float64")
                    final += displ_z[:, 0] * force[0]
                    final += displ_z[:, 2] * force[2]
                    data["Z"] = final

                if "R" in components:
                    final = np.zeros(displ_x.shape[0], dtype="float64")
                    final += displ_x[:, 0] * force[0]
                    final += displ_x[:, 2] * force[2]
                    data["R"] = final

                if "T" in components:
                    final = np.zeros(displ_x.shape[0], dtype="float64")
                    final += displ_x[:, 1] * force[1]
                    data["T"] = final

                for comp in ["E", "N"]:
                    if comp not in components:
                        continue

                    fac_1 = fac_1_map[comp](rotmesh_phi)
                    fac_2 = fac_2_map[comp](rotmesh_phi)

                    final = np.zeros(displ_x.shape[0], dtype="float64")
                    final += displ_x[:, 0] * force[0] * fac_1
                    final += displ_x[:, 1] * force[1] * fac_2
                    final += displ_x[:, 2] * force[2] * fac_1
                    if comp == "N":
                        final *= -1.0
                    data[comp] = final

            else:
                raise NotImplementedError

        else:
            if not isinstance(source, Source):
                raise NotImplementedError
            if self.info.dump_type != 'displ_only':
                raise NotImplementedError

            displ_1 = self.__get_displacement(self.meshes.m1, id_elem,
                                              gll_point_ids, col_points_xi,
                                              col_points_eta, xi, eta)
            displ_2 = self.__get_displacement(self.meshes.m2, id_elem,
                                              gll_point_ids, col_points_xi,
                                              col_points_eta, xi, eta)
            displ_3 = self.__get_displacement(self.meshes.m3, id_elem,
                                              gll_point_ids, col_points_xi,
                                              col_points_eta, xi, eta)
            displ_4 = self.__get_displacement(self.meshes.m4, id_elem,
                                              gll_point_ids, col_points_xi,
                                              col_points_eta, xi, eta)

            mij = source.tensor / self.parsed_mesh.amplitude
            # mij is [m_rr, m_tt, m_pp, m_rt, m_rp, m_tp]
            # final is in s, phi, z coordinates
            final = np.zeros((displ_1.shape[0], 3), dtype="float64")

            final[:, 0] += displ_1[:, 0] * mij[0]
            final[:, 2] += displ_1[:, 2] * mij[0]

            final[:, 0] += displ_2[:, 0] * (mij[1] + mij[2])
            final[:, 2] += displ_2[:, 2] * (mij[1] + mij[2])

            fac_1 = mij[3] * np.cos(rotmesh_phi) \
                + mij[4] * np.sin(rotmesh_phi)
            fac_2 = -mij[3] * np.sin(rotmesh_phi) \
                + mij[4] * np.cos(rotmesh_phi)

            final[:, 0] += displ_3[:, 0] * fac_1
            final[:, 1] += displ_3[:, 1] * fac_2
            final[:, 2] += displ_3[:, 2] * fac_1

            fac_1 = (mij[1] - mij[2]) * np.cos(2 * rotmesh_phi) \
                + 2 * mij[5] * np.sin(2 * rotmesh_phi)
            fac_2 = -(mij[1] - mij[2]) * np.sin(2 * rotmesh_phi) \
                + 2 * mij[5] * np.cos(2 * rotmesh_phi)

            final[:, 0] += displ_4[:, 0] * fac_1
            final[:, 1] += displ_4[:, 1] * fac_2
            final[:, 2] += displ_4[:, 2] * fac_1

            rotmesh_colat = np.arctan2(rotmesh_s, rotmesh_z)

            if "T" in components:
                # need the - for consistency with reciprocal mode,
                # need external verification still
                data["T"] = -final[:, 1]

            if "R" in components:
                data["R"] = final[:, 0] * np.cos(rotmesh_colat) \
                    - final[:, 2] * np.sin(rotmesh_colat)

            if "N" in components or "E" in components or "Z" in components:
                # transpose needed because rotations assume different slicing
                # (ugly)
                final = rotations.rotate_vector_src_to_NEZ(
                    final.T, rotmesh_phi,
                    source.longitude_rad, source.colatitude_rad,
                    receiver.longitude_rad, receiver.colatitude_rad).T

                if "N" in components:
                    data["N"] = final[:, 0]
                if "E" in components:
                    data["E"] = final[:, 1]
                if "Z" in components:
                    data["Z"] = final[:, 2]

        return data

    def __get_strain_interp(self, mesh, id_elem, gll_point_ids, G, GT,
                            col_points_xi, col_points_eta, corner_points,
                            eltype, axis, xi, eta):
        if id_elem not in mesh.strain_buffer:
            # Single precision in the NetCDF files but the later interpolation
            # routines require double precision. Assignment to this array will
            # force a cast.
            utemp = np.zeros((mesh.ndumps, mesh.npol + 1, mesh.npol + 1, 3),
                             dtype=np.float64, order="F")

            mesh_dict = mesh.f["Snapshots"]

            # Load displacement from all GLL points.
            for i, var in enumerate(["disp_s", "disp_p", "disp_z"]):
                if var not in mesh_dict:
                    continue

                # The netCDF Python wrappers starting with version 1.1.6
                # disallow duplicate and unordered indices while slicing. So
                # we need to do it manually.
                # The list of ids we have is unique but not sorted.
                ids = gll_point_ids.flatten()
                s_ids = np.sort(ids)
                temp = mesh_dict[var][:, s_ids]
                for ipol in range(mesh.npol + 1):
                    for jpol in range(mesh.npol + 1):
                        idx = ipol * 5 + jpol
                        utemp[:, jpol, ipol, i] = \
                            temp[:, np.argwhere(s_ids == ids[idx])[0][0]]

            strain_fct_map = {
                "monopole": sem_derivatives.strain_monopole_td,
                "dipole": sem_derivatives.strain_dipole_td,
                "quadpole": sem_derivatives.strain_quadpole_td}

            strain = strain_fct_map[mesh.excitation_type](
                utemp, G, GT, col_points_xi, col_points_eta, mesh.npol,
                mesh.ndumps, corner_points, eltype, axis)

            mesh.strain_buffer.add(id_elem, strain)
        else:
            strain = mesh.strain_buffer.get(id_elem)

        final_strain = np.empty((strain.shape[0], 6), order="F")

        for i in range(6):
            final_strain[:, i] = spectral_basis.lagrange_interpol_2D_td(
                col_points_xi, col_points_eta, strain[:, :, :, i], xi, eta)

        if not mesh.excitation_type == "monopole":
            final_strain[:, 3] *= -1.0
            final_strain[:, 5] *= -1.0

        return final_strain

    def __get_strain(self, mesh, id_elem):
        if id_elem not in mesh.strain_buffer:
            strain_temp = np.zeros((self.info.npts, 6), order="F")

            mesh_dict = mesh.f["Snapshots"]

            for i, var in enumerate([
                    'strain_dsus', 'strain_dsuz', 'strain_dpup',
                    'strain_dsup', 'strain_dzup', 'straintrace']):
                if var not in mesh_dict:
                    continue
                strain_temp[:, i] = mesh_dict[var][:, id_elem]

            # transform strain to voigt mapping
            # dsus, dpup, dzuz, dzup, dsuz, dsup
            final_strain = np.empty((self.info.npts, 6), order="F")
            final_strain[:, 0] = strain_temp[:, 0]
            final_strain[:, 1] = strain_temp[:, 2]
            final_strain[:, 2] = (strain_temp[:, 5] - strain_temp[:, 0] -
                                  strain_temp[:, 2])
            final_strain[:, 3] = -strain_temp[:, 4]
            final_strain[:, 4] = strain_temp[:, 1]
            final_strain[:, 5] = -strain_temp[:, 3]
            mesh.strain_buffer.add(id_elem, final_strain)
        else:
            final_strain = mesh.strain_buffer.get(id_elem)

        return final_strain

    def __get_displacement(self, mesh, id_elem, gll_point_ids, col_points_xi,
                           col_points_eta, xi, eta):
        if id_elem not in mesh.displ_buffer:
            utemp = np.zeros((mesh.ndumps, mesh.npol + 1, mesh.npol + 1, 3),
                             dtype=np.float64, order="F")

            mesh_dict = mesh.f["Snapshots"]

            # Load displacement from all GLL points.
            for i, var in enumerate(["disp_s", "disp_p", "disp_z"]):
                if var not in mesh_dict:
                    continue
                # The netCDF Python wrappers starting with version 1.1.6
                # disallow duplicate and unordered indices while slicing. So
                # we need to do it manually.
                # The list of ids we have is unique but not sorted.
                ids = gll_point_ids.flatten()
                s_ids = np.sort(ids)
                temp = mesh_dict[var][:, s_ids]
                for ipol in range(mesh.npol + 1):
                    for jpol in range(mesh.npol + 1):
                        idx = ipol * 5 + jpol
                        utemp[:, jpol, ipol, i] = \
                            temp[:, np.argwhere(s_ids == ids[idx])[0][0]]

            mesh.displ_buffer.add(id_elem, utemp)
        else:
            utemp = mesh.displ_buffer.get(id_elem)

        final_displacement = np.empty((utemp.shape[0], 3), order="F")

        for i in range(3):
            final_displacement[:, i] = spectral_basis.lagrange_interpol_2D_td(
                col_points_xi, col_points_eta, utemp[:, :, :, i], xi, eta)

        return final_displacement

    def _get_info(self):
        """
        Returns a dictionary with information about the currently loaded
        database.
        """
        # Get the size of all netCDF files.
        filesize = 0
        for m in self.meshes:
            if m:
                filesize += os.path.getsize(m.filename)

        if self._is_reciprocal:
            if self.meshes.pz is not None and self.meshes.px is not None:
                components = 'vertical and horizontal'
            elif self.meshes.pz is None and self.meshes.px is not None:
                components = 'horizontal only'
            elif self.meshes.pz is not None and self.meshes.px is None:
                components = 'vertical only'
        else:
            components = '4 elemental moment tensors'

        return dict(
            is_reciprocal=self._is_reciprocal,
            components=components,
            source_depth=float(self.parsed_mesh.source_depth)
            if self._is_reciprocal is False else None,
            velocity_model=self.parsed_mesh.background_model,
            external_model_name=self.parsed_mesh.external_model_name,
            attenuation=self.parsed_mesh.attenuation,
            period=float(self.parsed_mesh.dominant_period),
            dump_type=self.parsed_mesh.dump_type,
            excitation_type=self.parsed_mesh.excitation_type,
            dt=float(self.parsed_mesh.dt),
            sampling_rate=float(1.0 / self.parsed_mesh.dt),
            npts=int(self.parsed_mesh.ndumps),
            nfft=int(nextpow2(self.parsed_mesh.ndumps) * 2),
            length=float(self.parsed_mesh.dt * (self.parsed_mesh.ndumps - 1)),
            stf=self.parsed_mesh.stf_kind,
            src_shift=float(self.parsed_mesh.source_shift),
            src_shift_samples=int(self.parsed_mesh.source_shift_samp),
            slip=self.parsed_mesh.stf_norm,
            sliprate=self.parsed_mesh.stf_d_norm,
            spatial_order=int(self.parsed_mesh.npol),
            min_radius=float(self.parsed_mesh.kwf_rmin) * 1e3,
            max_radius=float(self.parsed_mesh.kwf_rmax) * 1e3,
            planet_radius=float(self.parsed_mesh.planet_radius),
            min_d=float(self.parsed_mesh.kwf_colatmin),
            max_d=float(self.parsed_mesh.kwf_colatmax),
            time_scheme=self.parsed_mesh.time_scheme,
            directory=os.path.relpath(self.db_path),
            filesize=filesize,
            compiler=self.parsed_mesh.axisem_compiler,
            user=self.parsed_mesh.axisem_user,
            format_version=int(self.parsed_mesh.file_version),
            axisem_version=self.parsed_mesh.axisem_version,
            datetime=self.parsed_mesh.creation_time
        )
