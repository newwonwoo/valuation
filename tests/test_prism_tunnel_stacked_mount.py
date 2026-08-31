from pathlib import Path

import pytest

import valuation_engine.tunnel_launcher as launcher


def test_stacked_equal_mount_points_fail_closed():
    mountinfo = (
        "20 1 8:1 / /mnt/state rw - ext4 /dev/sda1 rw\n"
        "21 20 0:44 / /mnt/state rw - cifs //server/share rw\n"
    )

    with pytest.raises(launcher.PrismTunnelError, match="ambiguous.*stacked"):
        launcher._filesystem_type_for_path(
            Path("/mnt/state/prism"),
            mountinfo_text=mountinfo,
        )


def test_single_most_specific_mount_still_resolves():
    mountinfo = (
        "20 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
        "21 20 8:2 / /srv/prism rw - xfs /dev/sdb1 rw\n"
    )

    fs_type, mount_point = launcher._filesystem_type_for_path(
        Path("/srv/prism/state"),
        mountinfo_text=mountinfo,
    )

    assert fs_type == "xfs"
    assert mount_point == Path("/srv/prism")
