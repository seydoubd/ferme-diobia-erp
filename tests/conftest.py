import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def app():
    d = tempfile.mkdtemp()
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False,
                      "SQLALCHEMY_DATABASE_URI": "sqlite:///" + os.path.join(d, "test.db")})
    with app.app_context():
        from app.utils import set_param
        from app.extensions import db
        set_param("connexion_obligatoire", "1")  # les tests de droits exigent une vraie connexion
        db.session.commit()
        yield app


@pytest.fixture()
def admin(app):
    from app.models import User
    return User.query.filter_by(role="admin").first()
