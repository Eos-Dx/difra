from difra.gui.archive_project_statistics import build_archive_project_statistics
from difra.gui.archive_project_statistics import normalize_specimen_key


def test_normalize_specimen_key_uses_trailing_matador_id():
    assert normalize_specimen_key("378766__377655_P106_WH_S03") == "377655"
    assert normalize_specimen_key(" 337503 ") == "337503"
    assert normalize_specimen_key("P146") == "P146"


def test_archive_project_statistics_compares_archive_and_matador_sets():
    rows = [
        {
            "specimenId": "5001__1001_A",
            "matadorProjectId": "32405",
            "project_id": "Project 6",
            "matadorStudyId": "377501",
            "transfer_status": "SENT",
            "has_measurements": True,
        },
        {
            "specimenId": "5001__1002_B",
            "matadorProjectId": "32405",
            "project_id": "Project 6",
            "matadorStudyId": "377501",
            "transfer_status": "UNSENT",
            "has_measurements": True,
        },
    ]

    stats = build_archive_project_statistics(
        rows,
        matador_specimens_by_project={"32405": {"1001", "1002", "1003"}},
        matador_uploaded_by_project={"32405": {"1001"}},
    )

    project = stats.projects[0]
    assert project["archiveMeasured"] == 2
    assert project["matadorSpecimens"] == 3
    assert project["matadorUploaded"] == 1
    assert project["missingInArchive"] == 1
    assert project["notUploaded"] == 1

    rows_by_id = {
        row["specimenId"]: row for row in stats.specimens_by_project["32405"]
    }
    assert rows_by_id["1001"]["localStatus"] == "Sent"
    assert rows_by_id["1001"]["matadorMeasurement"] == "In"
    assert rows_by_id["1002"]["localStatus"] == "Unsent"
    assert rows_by_id["1002"]["matadorMeasurement"] == "Out"
    assert rows_by_id["1003"]["localMeasured"] is False
