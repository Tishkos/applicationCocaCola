"""Each genset's share of the gensets' power, from the site master's load sharing (plantcollector/genshare.py)."""
from datetime import datetime, timedelta, timezone

from plantcollector.genshare import GensetShares


def shares(**kw):
    return GensetShares("neo-01", [1, 2, 3, 4, 5], min_seen=1, **kw)


def on(*gs):
    return {g: g in gs for g in (1, 2, 3, 4, 5)}


def test_equal_split_before_anything_is_known():
    s = shares()
    assert s.split(900.0, on(4, 5)) == {1: 0.0, 2: 0.0, 3: 0.0, 4: 450.0, 5: 450.0}


def test_split_follows_the_nominals_once_learnt():
    s = shares()
    s.observe(0b10000, 1400.0)                 # Gen 5 alone: 1,400 kW
    s.observe(0b11000, 2550.0)                 # Gen 4 and Gen 5: Gen 4 is 1,150 kW
    assert s.nominals() == {5: 1400.0, 4: 1150.0}
    sp = s.split(1275.0, on(4, 5), 2550.0)
    assert sp[5] == 700.0 and sp[4] == 575.0 and sp[1] == sp[2] == sp[3] == 0.0
    assert sp[4] + sp[5] == 1275.0


def test_one_more_than_a_set_seen_tells_that_genset():
    s = shares()
    s.observe(0b11000, 2550.0)                 # Gen 4 + 5
    s.observe(0b11100, 3700.0)                 # Gen 3 + 4 + 5: Gen 3 is 1,150 kW
    assert s.nominals() == {3: 1150.0}
    # Gen 4 and 5 still unknown: they split what is left of the running nominal equally
    sp = s.split(1850.0, on(3, 4, 5), 3700.0)
    assert sp[3] == 575.0 and sp[4] == sp[5] == 637.5


def test_unknown_breaker_or_power_gives_no_split():
    s = shares()
    b = on(4, 5)
    b[2] = None
    assert set(s.split(900.0, b).values()) == {None}
    assert set(s.split(None, on(4, 5)).values()) == {None}


def test_no_genset_on_the_bus():
    s = shares()
    assert s.split(0.0, on()) == {g: 0.0 for g in (1, 2, 3, 4, 5)}
    assert set(s.split(800.0, on()).values()) == {None}       # power without a breaker closed: not believed


def test_implausible_running_nominal_is_ignored():
    s = shares()
    s.observe(0b10000, 0.0)                    # "In PM" style zero
    s.observe(0b10000, 99999.0)
    assert s.nominals() == {}


def test_needs_two_readings_by_default():
    s = GensetShares("neo-01", [1, 2])
    s.observe(0b01, 1100.0)
    assert s.nominals() == {}
    s.observe(0b01, 1100.0)
    assert s.nominals() == {1: 1100.0}


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def history(self, did, pid, since, until):
        return [(t.isoformat(), v) for t, v in self.rows.get(pid, []) if since <= t <= until]


def test_seed_from_history_skips_readings_right_after_a_breaker_moved():
    t0 = datetime(2026, 11, 7, 6, 0, tzinfo=timezone.utc)
    m = lambda s: t0 + timedelta(seconds=s)
    rows = {f"g{g}_running": [(m(0), 0.0)] for g in (1, 2, 3)}
    rows["g3_running"] = [(m(0), 1.0)]                        # Gen 3 alone from the start
    rows["g2_running"] = [(m(0), 0.0), (m(600), 1.0)]         # Gen 2 joins at 10 min
    rows["running_nominal_kw"] = [(m(60), 1150.0), (m(120), 1150.0),
                                  (m(605), 9999.0),           # 5 s after Gen 2 closed: not used
                                  (m(660), 2550.0), (m(720), 2550.0)]
    s = GensetShares("neo-01", [1, 2, 3])
    used = s.seed(FakeStore(rows), t0, m(3600))
    assert used == 4
    assert s.nominals() == {3: 1150.0, 2: 1400.0}


def test_collector_live_point_has_a_column_per_genset(tmp_path):
    import os
    from plantcollector.core import Collector
    cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "site.demo.yaml")
    c = Collector(cfg, str(tmp_path), fresh=True)
    cols = c.live_columns()
    assert cols[:5] == ["t", "load_kw", "pv_kw", "genset_kw", "mains_kw"]
    gcols = [x for x in cols[5:]]
    assert gcols and all(x.startswith("g") and x.endswith("_kw") for x in gcols)
    p = c.live_point()
    assert len(p) == len(cols)
    shares_now = [v for v in p[5:] if v is not None]
    total = c.store.value(c.genshares.master_id, "power_kw")
    if shares_now and total is not None:
        assert abs(sum(shares_now) - total) < 1.0


def test_weights_add_up_and_split_the_kvar_too(tmp_path):
    import os
    from datetime import datetime, timezone
    from plantcollector.core import Collector
    from plantcollector.store import Reading
    cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "site.demo.yaml")
    c = Collector(cfg, str(tmp_path), initial_poll=False, fresh=True)
    c.genshares.seeded = True
    ts = datetime.now(timezone.utc)
    vals = {"power_kw": 1275.0, "gensets_q_kvar": 510.0, "running_nominal_kw": 2550.0}
    vals.update({f"g{g}_running": (1.0 if g in (4, 5) else 0.0) for g in c.genshares.numbers})
    c.store.ingest("neo-01", {p: Reading(p, v, "", ts) for p, v in vals.items()})
    c.genshares.observe(0b10000, 1400.0)
    c.genshares.observe(0b10000, 1400.0)
    each = {e["n"]: e for e in c.genset_each()}
    assert each[5]["kw"] == 700.0 and each[4]["kw"] == 575.0 and each[1]["kw"] == 0.0
    assert each[5]["kvar"] == 280.0 and each[4]["kvar"] == 230.0 and each[1]["kvar"] == 0.0
    from plantcollector import views
    assert views.summary(c)["kpi"]["gensets_each"] == c.genset_each()


def test_weights_none_without_breakers():
    s = shares()
    assert s.weights(on()) is None
    w = s.weights(on(1, 2, 3))
    assert abs(sum(w.values()) - 1) < 1e-9 and w[4] == w[5] == 0.0
