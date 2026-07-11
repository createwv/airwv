"""The reporting tables exist and accept rows (Report / CommunityReading / Feedback)."""
from airwv.storage import Store
from airwv.storage.models import CommunityReading, Feedback, Report


def test_reporting_tables_created_and_writable(tmp_path):
    store = Store(f"sqlite:///{tmp_path/'r.sqlite'}")
    store.create_schema()
    with store._session_factory() as s:
        s.add(Report(domain="air", category="odor", description="chemical smell",
                     lat=38.35, lon=-81.63))
        s.add(Feedback(kind="bug", message="map won't load"))
        s.add(CommunityReading(domain="water", parameter="pH", value=6.4, unit="pH"))
        s.commit()
    with store._session_factory() as s:
        r = s.query(Report).one()
        assert r.stage == "published_unverified"   # default staged trust state
        assert r.org_public is False and r.photo_ok is False
        assert s.query(Feedback).one().status == "new"
        assert s.query(CommunityReading).one().parameter == "pH"
