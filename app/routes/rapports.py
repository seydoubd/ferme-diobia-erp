from flask import Blueprint, render_template, request, send_file, abort
from flask_login import login_required, current_user
from ..permissions import require
from ..services import rapports as svc
from . import periode_args

bp = Blueprint("rapports", __name__, url_prefix="/rapports")


@bp.before_request
@login_required
@require("rapports.voir")
def _auth():
    pass


@bp.route("/")
def index():
    debut, fin = periode_args("mois")
    dispo = {k: v for k, v in svc.RAPPORTS.items() if not v.get("finances") or current_user.can("finances.voir")}
    return render_template("rapports/index.html", rapports=dispo, debut=debut, fin=fin)


@bp.route("/<code>")
def voir(code):
    if code not in svc.RAPPORTS:
        abort(404)
    if svc.RAPPORTS[code].get("finances") and not current_user.can("finances.voir"):
        abort(403)
    debut, fin = periode_args("mois")
    rep = svc.generer(code, debut, fin)
    fmt = request.args.get("format")
    if fmt == "xlsx":
        return send_file(svc.to_xlsx(rep), as_attachment=True, download_name=f"{code}_{debut}_{fin}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "pdf":
        return send_file(svc.to_pdf(rep), as_attachment=True, download_name=f"{code}_{debut}_{fin}.pdf",
                         mimetype="application/pdf")
    return render_template("rapports/voir.html", rep=rep, code=code, debut=debut, fin=fin)
