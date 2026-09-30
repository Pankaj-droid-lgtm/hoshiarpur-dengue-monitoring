import io
import tempfile
import unittest
from datetime import date
from openpyxl import Workbook

from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (
    Block, Deployment, GpsCapture, House, HouseAssignment, HouseVisit,
    LarvalObservation, Locality, Photo, ReinspectionTask, User, Worker,
)
from app.services.historical_import import import_historical_workbook


class TestConfig:
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_DIRECTORY = tempfile.mkdtemp(prefix="dengue-test-uploads-")
    TESTING = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False


class OperationalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            admin = User(username="admin-test", password_hash=generate_password_hash("Admin-password-123"), role="admin")
            adc = User(username="adc-test", password_hash=generate_password_hash("Adc-password-12345"), role="adc")
            first_account = User(username="W001", password_hash=generate_password_hash("Worker-password-123"), role="field_worker")
            second_account = User(username="W002", password_hash=generate_password_hash("Worker-password-234"), role="field_worker")
            first_worker = Worker(official_worker_id="W001", full_name="Test Worker One", designation="Field Worker", user=first_account)
            second_worker = Worker(official_worker_id="W002", full_name="Test Worker Two", designation="Field Worker", user=second_account)
            block = Block(name="Test Block")
            db.session.add_all([admin, adc, first_worker, second_worker, block])
            db.session.flush()
            locality = Locality(block=block, name="Test Locality")
            house = House(locality=locality, house_code="HOS-TEST-001", address="Test house")
            db.session.add_all([locality, house])
            db.session.commit()
            self.ids = {"first_worker": first_worker.id, "second_worker": second_worker.id, "block": block.id, "locality": locality.id, "house": house.id}

    def tearDown(self):
        with self.app.app_context():
            db.drop_all()

    def post(self, path, data=None, **kwargs):
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "test-csrf"
        return self.client.post(path, data={"csrf_token": "test-csrf", **(data or {})}, **kwargs)

    def login(self, username, password):
        return self.post("/auth/login", {"username": username, "password": password})

    def create_deployment(self, worker_id):
        response = self.post("/deployments/new", {"deployment_date": date.today().isoformat(), "worker_id": str(worker_id), "team_name": f"Team {worker_id}", "block_id": str(self.ids["block"]), "locality_id": str(self.ids["locality"])})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            return Deployment.query.order_by(Deployment.id.desc()).first().id

    def test_unique_worker_and_house_identifiers(self):
        with self.app.app_context():
            db.session.add(Worker(official_worker_id="W001", full_name="Duplicate", designation="Field Worker"))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            db.session.add(House(locality_id=self.ids["locality"], house_code="HOS-TEST-001", address="Duplicate house"))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_assignment_isolation_reassignment_and_completion(self):
        self.assertEqual(self.login("admin-test", "Admin-password-123").status_code, 302)
        first_deployment = self.create_deployment(self.ids["first_worker"])
        with self.app.app_context():
            worker = db.session.get(Worker, self.ids["second_worker"])
            replacement = Deployment(
                worker=worker, account_user=worker.user, worker_name=worker.full_name,
                worker_code=worker.official_worker_id, worker_designation=worker.designation,
                block_id=self.ids["block"], locality_id=self.ids["locality"],
                deployment_date=date.today(), team_name="Replacement Team",
                assigned_by_user_id=User.query.filter_by(username="admin-test").one().id,
            )
            db.session.add(replacement)
            db.session.commit()
            second_deployment = replacement.id
        self.assertIn(b"active assignment for this date", self.post(f"/deployments/{second_deployment}/assignments", {"house_id": str(self.ids["house"])}, follow_redirects=True).data)
        with self.app.app_context():
            assignment = HouseAssignment.query.filter_by(deployment_id=first_deployment).one()
            assignment_id = assignment.id
        self.post("/auth/logout")
        self.assertEqual(self.login("W002", "Worker-password-234").status_code, 302)
        self.assertEqual(self.client.get(f"/deployments/{first_deployment}").status_code, 403)
        self.assertEqual(self.client.get(f"/visits/assignments/{assignment_id}").status_code, 403)
        self.post("/auth/logout")
        self.assertEqual(self.login("admin-test", "Admin-password-123").status_code, 302)
        self.assertEqual(self.post(f"/deployments/assignments/{assignment_id}/reassign", {"destination_deployment_id": str(second_deployment)}).status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(HouseAssignment, assignment_id).deployment_id, second_deployment)
        self.post("/auth/logout")
        self.assertEqual(self.login("W001", "Worker-password-123").status_code, 302)
        self.assertEqual(self.client.get(f"/visits/assignments/{assignment_id}").status_code, 403)
        self.post("/auth/logout")
        self.assertEqual(self.login("W002", "Worker-password-234").status_code, 302)
        photo = (io.BytesIO(b"\x89PNG\r\n\x1a\nverification-image"), "visit.png", "image/png")
        self.assertEqual(self.post(f"/visits/assignments/{assignment_id}", {
            "visit_outcome": "completed", "containers_checked": "1", "positive_containers": "1",
            "container_type": "tank", "larvae_found": "yes", "latitude": "31.5",
            "longitude": "75.9", "gps_accuracy": "5", "remarks": "Test observation",
            "photo": photo,
        }, content_type="multipart/form-data").status_code, 302)
        with self.app.app_context():
            self.assertEqual(db.session.get(Deployment, second_deployment).status, "completed")
            self.assertEqual(HouseVisit.query.count(), 1)
            self.assertEqual(GpsCapture.query.count(), 1)
            self.assertEqual(Photo.query.count(), 1)
            self.assertEqual(LarvalObservation.query.count(), 1)
            self.assertEqual(ReinspectionTask.query.count(), 1)
        self.post("/auth/logout")
        self.assertEqual(self.login("adc-test", "Adc-password-12345").status_code, 302)
        self.assertEqual(self.client.get("/monitoring/").status_code, 200)
        self.assertEqual(self.client.get("/monitoring/reports/historical-vbd.csv").status_code, 200)
        self.assertEqual(self.client.get("/monitoring/reports/historical-field-responses.csv").status_code, 200)
        self.assertEqual(self.client.get("/monitoring/reports/high-risk-areas.csv").status_code, 200)
        self.assertEqual(self.post("/deployments/new", {}).status_code, 403)
        self.assertEqual(self.post(f"/deployments/{first_deployment}/assignments", {"house_id": str(self.ids["house"])}).status_code, 403)

    def test_historical_import_is_idempotent_and_preserves_operational_data(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        sheet.append(["Confirmed dengue cases linelisting - district Hoshiarpur"])
        sheet.append(["S.N.", "Name of the patient", "Age (in years)", "Sex/ Gender", "Contact number", "Address", "Rural/ Urban", "If Rural name of the block", "If Urban, Name of the Town", "Date of testing"])
        sheet.append([1, "Case test", 30, "F", None, "Source address", "Rural", "Source Block", None, "01.01.2026"])
        contents = io.BytesIO()
        workbook.save(contents)
        with self.app.app_context():
            first = import_historical_workbook("Dengue cases test.xlsx", contents.getvalue(), "case_line_list")
            db.session.commit()
            second = import_historical_workbook("Dengue cases test.xlsx", contents.getvalue(), "case_line_list")
            self.assertEqual(first.inserted, 1)
            self.assertEqual(second.skipped, 1)
            self.assertEqual(Worker.query.count(), 2)
            self.assertEqual(HouseVisit.query.count(), 0)
            self.assertEqual(House.query.count(), 1)

    def test_daily_task_distributes_eligible_houses_to_selected_workers(self):
        self.assertEqual(self.login("admin-test", "Admin-password-123").status_code, 302)
        with self.app.app_context():
            db.session.add(House(locality_id=self.ids["locality"], house_code="HP-HOS-000002", address="Second test house"))
            db.session.commit()
        response = self.post("/deployments/new", {
            "deployment_date": date.today().isoformat(),
            "worker_ids": [str(self.ids["first_worker"]), str(self.ids["second_worker"])],
            "block_id": str(self.ids["block"]),
            "locality_id": str(self.ids["locality"]),
            "team_name": "Team 1",
            "risk_filter": "all",
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            deployments = Deployment.query.order_by(Deployment.worker_id).all()
            self.assertEqual(len(deployments), 2)
            self.assertEqual(HouseAssignment.query.count(), 2)
            self.assertEqual(sorted(len(item.house_assignments) for item in deployments), [1, 1])
        dashboard = self.client.get(f"/deployments/?date={date.today().isoformat()}")
        self.assertIn(b"Team 1", dashboard.data)
        self.assertEqual(self.client.get(
            f"/deployments/eligible-house-count?block_id={self.ids['block']}&locality_id={self.ids['locality']}&deployment_date={date.today().isoformat()}"
        ).get_json(), {"count": 0})
        self.post("/auth/logout")
        self.assertEqual(self.login("W001", "Worker-password-123").status_code, 302)
        mobile = self.client.get("/deployments/mobile")
        self.assertIn(b"Team 1", mobile.data)
        self.assertIn(b"W001, W002", mobile.data)
        self.assertIn(b"My houses", mobile.data)


if __name__ == "__main__":
    unittest.main()
