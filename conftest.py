from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import collections
import pickle
import os
import shutil
import tempfile
import time


TEST_DATA = os.path.join(os.path.dirname(__file__), "instaseis", "tests",
                         "data")

def repack_databases():
    """
    Repack databases and create a couple temporary test databases.

    It will generate various repacked databases and use them in the test
    suite - this for one tests the repacking but also that Instaseis can
    work with a number of different database layouts.
    """
    try:
        import netCDF4
        import click
    except ImportError:
        print("\nSkipping database repacking tests which require `click` and "
              "`netCDF4` to be installed.\n")
        return {
            "root_folder": None,
            "databases": {}
        }

    import h5py
    from instaseis.scripts.repack_db import repack_file

    root_folder = tempfile.mkdtemp()

    # First create a transposed database - make it contiguous.
    transposed_bw_db = os.path.join(
        root_folder, "transposed_100s_db_bwd_displ_only")
    os.makedirs(transposed_bw_db)

    db = os.path.join(TEST_DATA, "100s_db_bwd_displ_only")
    f = "ordered_output.nc4"
    px = os.path.join(db, "PX", "Data", f)
    pz = os.path.join(db, "PZ", "Data", f)

    px_tr = os.path.join(transposed_bw_db, "PX", f)
    pz_tr = os.path.join(transposed_bw_db, "PZ", f)

    os.makedirs(os.path.dirname(px_tr))
    os.makedirs(os.path.dirname(pz_tr))

    repack_file(input_filename=px, output_filename=px_tr, contiguous=True,
                compression_level=None, quiet=True, transpose=True)
    repack_file(input_filename=pz, output_filename=pz_tr, contiguous=True,
                compression_level=None, quiet=True, transpose=True)

    # Now transpose it again which should result in the original layout.
    transposed_and_back_bw_db = os.path.join(
        root_folder, "transposed_and_back_100s_db_bwd_displ_only")
    os.makedirs(transposed_and_back_bw_db)

    px_tr_and_back = os.path.join(transposed_and_back_bw_db, "PX", f)
    pz_tr_and_back = os.path.join(transposed_and_back_bw_db, "PZ", f)
    os.makedirs(os.path.dirname(px_tr_and_back))
    os.makedirs(os.path.dirname(pz_tr_and_back))

    repack_file(input_filename=px_tr, output_filename=px_tr_and_back,
                contiguous=False, compression_level=4, quiet=True,
                transpose=True)
    repack_file(input_filename=pz_tr, output_filename=pz_tr_and_back,
                contiguous=False, compression_level=4, quiet=True,
                transpose=True)

    # Now add another simple repacking test - repack the original one and
    # repack the transposed one.
    repacked_bw_db = os.path.join(
        root_folder, "repacked_100s_db_bwd_displ_only")
    os.makedirs(repacked_bw_db)

    px_r = os.path.join(repacked_bw_db, "PX", f)
    pz_r = os.path.join(repacked_bw_db, "PZ", f)

    os.makedirs(os.path.dirname(px_r))
    os.makedirs(os.path.dirname(pz_r))

    repack_file(input_filename=px, output_filename=px_r, contiguous=True,
                compression_level=None, quiet=True, transpose=False)
    repack_file(input_filename=pz, output_filename=pz_r, contiguous=True,
                compression_level=None, quiet=True, transpose=False)

    # Also repack the transposed database.
    repacked_transposed_bw_db = os.path.join(
        root_folder, "repacked_transposed_100s_db_bwd_displ_only")
    os.makedirs(repacked_transposed_bw_db)

    px_r_tr = os.path.join(repacked_transposed_bw_db, "PX", f)
    pz_r_tr = os.path.join(repacked_transposed_bw_db, "PZ", f)

    os.makedirs(os.path.dirname(px_r_tr))
    os.makedirs(os.path.dirname(pz_r_tr))

    repack_file(input_filename=px_tr, output_filename=px_r_tr, contiguous=True,
                compression_level=None, quiet=True, transpose=False)
    repack_file(input_filename=pz_tr, output_filename=pz_r_tr, contiguous=True,
                compression_level=None, quiet=True, transpose=False)

    # Actually test the shapes of the fields to see that something happened.
    with h5py.File(pz, mode="r") as f:
        original_shape = f["Snapshots"]["disp_z"].shape
    with h5py.File(pz_tr, mode="r") as f:
        transposed_shape = f["Snapshots"]["disp_z"].shape
    with h5py.File(pz_tr_and_back, mode="r") as f:
        transposed_and_back_shape = f["Snapshots"]["disp_z"].shape
    with h5py.File(pz_r, mode="r") as f:
        repacked_shape = f["Snapshots"]["disp_z"].shape
    with h5py.File(pz_r_tr, mode="r") as f:
        repacked_transposed_shape = f["Snapshots"]["disp_z"].shape

    assert original_shape == tuple(reversed(transposed_shape))
    assert original_shape == transposed_and_back_shape
    assert original_shape == repacked_shape
    assert original_shape == tuple(reversed(repacked_transposed_shape))

    dbs = collections.OrderedDict()
    # Important is that the name is fairly similar to the original
    # as some tests use the patterns in the name.
    dbs["transposed_100s_db_bwd_displ_only"] = transposed_bw_db
    dbs["transposed_and_back_100s_db_bwd_displ_only"] = \
        transposed_and_back_bw_db
    dbs["repacked_100s_db_bwd_displ_only"] = repacked_bw_db
    dbs["repacked_transposed_100s_db_bwd_displ_only"] = \
        repacked_transposed_bw_db

    return {
        "root_folder": root_folder,
        "databases": dbs
    }


def is_master(config):
    """
    Returns True/False if the current node is the master node.

    Only applies to if run with pytest-xdist.
    """
    # This attribute is only set on slaves.
    if hasattr(config, "slaveinput"):
        return False
    else:
        return True


def pytest_configure(config):
    if is_master(config):
        config.dbs = repack_databases()
    else:
        while True:
            if "dbs" not in config.slaveinput:
                time.sleep(0.01)
                continue
            break
        config.dbs = pickle.loads(config.slaveinput["dbs"])


def pytest_configure_node(node):
    """
    This is only called on the master - we use it to send the information to
    all the slaves.

    Only applies to if run with pytest-xdist.
    """
    node.slaveinput["dbs"] = pickle.dumps(node.config.dbs)


def pytest_unconfigure(config):
    if is_master(config) and config.dbs["root_folder"]:
        if os.path.exists(config.dbs["root_folder"]):
            shutil.rmtree(config.dbs["root_folder"])
