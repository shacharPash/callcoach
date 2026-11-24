from callcoach.cpu import SAMPLE_EVERY_S, CpuMeter, tree_cpu

TABLE = """  10     1  12.5
  11    10  40,0
  12    11   7.5
  13     1  99.0
 bad row
"""


def test_the_tree_is_the_root_and_its_descendants_only():
    assert tree_cpu(10, TABLE) == 60.0 and tree_cpu(12, TABLE) == 7.5 and tree_cpu(99, TABLE) == 0.0


def test_the_meter_is_a_share_of_the_mac_and_samples_rarely():
    reads, now = [], [0.0]

    def read():
        reads.append(1)
        return TABLE

    meter = CpuMeter(root=10, read=read, cores=4, clock=lambda: now[0])
    assert meter.value() == 15.0 and meter.value() == 15.0 and len(reads) == 1
    now[0] += SAMPLE_EVERY_S
    meter.value()
    assert len(reads) == 2


def test_a_ps_that_fails_reads_as_unknown():
    def broken():
        raise OSError("no ps")

    assert CpuMeter(read=broken).value() is None
