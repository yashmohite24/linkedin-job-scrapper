from types import SimpleNamespace

from linkedin_jobs.store import SupabaseJobStore


class _Query:
    def __init__(self, recorder: dict):
        self.recorder = recorder
        self.mode = ""
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def in_(self, _column, values):
        self.recorder.setdefault("lookups", []).append(list(values))
        return self

    def upsert(self, payload, **kwargs):
        self.mode = "upsert"
        self.payload = payload
        self.recorder["upsert_kwargs"] = kwargs
        return self

    def execute(self):
        if self.mode == "select":
            known = {"1"}
            data = [{"linkedin_job_id": job_id} for job_id in self.recorder["lookups"][-1] if job_id in known]
            return SimpleNamespace(data=data)
        assert self.payload is not None
        data = [row for row in self.payload if row["linkedin_job_id"] != "1"]
        return SimpleNamespace(data=data)


class _Storage:
    def __init__(self):
        self.calls = []

    def from_(self, bucket):
        self.bucket = bucket
        return self

    def upload(self, path, file, file_options=None):
        self.calls.append((self.bucket, path, file, file_options))


class _Client:
    def __init__(self):
        self.recorder: dict = {}
        self.storage = _Storage()

    def table(self, name):
        assert name == "job_openings"
        return _Query(self.recorder)


def test_existing_ids_are_chunked_and_insert_skips_duplicates(tmp_path):
    client = _Client()
    store = SupabaseJobStore(client)
    job_ids = [str(number) for number in range(150)]

    found = store.existing_ids(job_ids)
    inserted = store.insert_new(
        [
            {"linkedin_job_id": "1", "job_url": "https://www.linkedin.com/jobs/view/1"},
            {"linkedin_job_id": "9", "job_url": "https://www.linkedin.com/jobs/view/9"},
        ]
    )

    assert found == {"1"}
    assert [len(chunk) for chunk in client.recorder["lookups"]] == [100, 50]
    assert client.recorder["upsert_kwargs"] == {
        "on_conflict": "linkedin_job_id",
        "ignore_duplicates": True,
    }
    assert [row["linkedin_job_id"] for row in inserted] == ["9"]

    path = tmp_path / "09.csv"
    path.write_text("company_name\nAcme\n", encoding="utf-8")
    store.upload_csv(path, "exports/2026/11/09/09.csv")
    bucket, key, payload, options = client.storage.calls[0]
    assert bucket == "job-exports"
    assert key == "exports/2026/11/09/09.csv"
    assert payload == b"company_name\nAcme\n"
    assert options["content-type"] == "text/csv"
    assert options["upsert"] == "true"


def test_empty_insert_response_is_not_treated_as_new():
    class EmptyInsert(_Query):
        def execute(self):
            return SimpleNamespace(data=[])

    class Client(_Client):
        def table(self, name):
            return EmptyInsert(self.recorder)

    store = SupabaseJobStore(Client())
    assert store.insert_new([{"linkedin_job_id": "4"}]) == []
