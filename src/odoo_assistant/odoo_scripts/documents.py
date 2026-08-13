#!/usr/bin/env python3
"""Documents and notifications — one way that works for everything.

Odoo stores every binary the same way: base64 in a field, usually on
`ir.attachment`. Reports, chatter attachments, product images, signatures,
imported files — same mechanism. So one function covers all of them.

    from documents import Documents
    d = Documents(odoo, company_id=2)

    d.attachments("account.move", 5775)          # what is attached
    d.download("account.move", 5775, "/tmp")     # save them all
    d.generate_pdf("account.move", 5775)         # print report -> attachment
    d.notify("sale.order", sid, "text", users=[2])   # chatter + inbox
    d.schedule("crm.lead", lid, "Call back", user_id=2, days=3)
"""
import base64
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_client import connect_cli as connect, OdooError, OdooExecutedButUnserializable  # noqa


class Documents:
    def __init__(self, odoo, company_id=None):
        self.o = odoo
        self.ctx = {"allowed_company_ids": [company_id]} if company_id else {}

    # ------------------------------------------------------------- binaries
    def attachments(self, model, res_id, mimetype=None):
        """Everything attached to a record — chatter files included.

        The chatter is not a separate store: a file dropped in the chatter is
        an `ir.attachment` with `res_model`/`res_id` pointing at the record.
        Same query for reports, imports and images.
        """
        dom = [["res_model", "=", model], ["res_id", "=", res_id]]
        if mimetype:
            dom.append(["mimetype", "=", mimetype])
        return self.o.search_read(
            "ir.attachment", dom,
            ["id", "name", "mimetype", "file_size", "create_date", "type"],
            context=self.ctx)

    def read_binary(self, att_id):
        """Bytes of one attachment.

        `datas` is base64 and works over XML-RPC even for large files —
        measured on an 8.4 MB attachment: 11.2 M chars of base64, decoded
        intact. `file_size` tells you the cost before you pay it.

        PITFALL: `datas` can come back EMPTY on a perfectly valid attachment.
        Odoo stores the bytes on disk (the filestore) and keeps only the path
        in `store_fname`; a database restored without its filestore directory
        has thousands of attachments whose metadata is intact and whose
        content is gone. Odoo returns empty rather than raising.

        Measured on the reference dev instance: 13 of 25 sampled attachments
        were unreadable this way. So distinguish "missing file" from "bug" —
        `missing_content()` tells you which.
        """
        rows = self.o.call("ir.attachment", "read", [[att_id]],
                           {"fields": ["name", "datas", "mimetype",
                                       "store_fname", "file_size"],
                            "context": self.ctx})
        if not rows:
            raise OdooError(f"attachment {att_id} does not exist")
        r = rows[0]
        if not r.get("datas"):
            where = r.get("store_fname") or "(no store_fname)"
            raise OdooError(
                f"attachment {att_id} '{r.get('name')}' has no content: "
                f"the record says {r.get('file_size')} bytes at filestore "
                f"path {where}, but the file is not there. The database was "
                f"most likely restored without its filestore. This is a data "
                f"problem, not a permissions or API problem.")
        return r["name"], base64.b64decode(r["datas"])

    def missing_content(self, model=None, sample=50):
        """How many attachments have lost their file. Answers the question
        'is the download broken, or is the data gone?'"""
        dom = [["type", "=", "binary"]]
        if model:
            dom.append(["res_model", "=", model])
        rows = self.o.search_read("ir.attachment", dom,
                                  ["id", "name", "file_size"],
                                  limit=sample, context=self.ctx)
        missing = []
        for r in rows:
            d = self.o.call("ir.attachment", "read", [[r["id"]]],
                            {"fields": ["datas"], "context": self.ctx})
            if not d or not d[0].get("datas"):
                missing.append(r)
        return {"sampled": len(rows), "missing": len(missing),
                "readable": len(rows) - len(missing),
                "examples": [m["name"] for m in missing[:5]]}

    def field_binary(self, model, res_id, field):
        """Bytes of a binary FIELD, not an attachment.

        Many documents never become attachments: they live in a field on the
        record — `invoice_pdf_report_file`, `image_1920`, `signature`,
        `datas` on ir.attachment itself. Same base64 convention.
        """
        rows = self.o.call(model, "read", [[res_id]],
                           {"fields": [field], "context": self.ctx})
        raw = rows[0].get(field) if rows else None
        if not raw:
            raise OdooError(f"{model}.{field} is empty on record {res_id}")
        return base64.b64decode(raw)

    def binary_fields(self, model):
        """Which fields on this model hold binaries. Discovery, not guesswork."""
        f = self.o.fields_get(model, [], ["type", "string"])
        return sorted(k for k, v in f.items() if v.get("type") == "binary")

    def download(self, model, res_id, dest_dir="/tmp", include_fields=True):
        """Save every document of a record to disk.

        Returns {"saved": [paths], "skipped": [(name, why)]}. It reports what
        it could NOT get instead of silently returning a short list — an
        earlier version returned 0 files for a record with 9 attachments and
        said nothing, which reads exactly like "there are no attachments".
        """
        os.makedirs(dest_dir, exist_ok=True)
        saved, skipped = [], []
        for a in self.attachments(model, res_id):
            try:
                name, blob = self.read_binary(a["id"])
            except OdooError as e:
                skipped.append((a["name"], str(e).split(":", 1)[-1].strip()[:70]))
                continue
            p = os.path.join(dest_dir, re.sub(r"[/\\]", "_", name))
            with open(p, "wb") as fh:
                fh.write(blob)
            saved.append(p)

        if include_fields:
            for field in self.binary_fields(model):
                if field in ("datas", "db_datas", "raw"):
                    continue
                try:
                    blob = self.field_binary(model, res_id, field)
                except OdooError:
                    continue          # empty field is normal, not worth noting
                ext = "pdf" if blob[:4] == b"%PDF" else "bin"
                p = os.path.join(dest_dir, f"{model}_{res_id}_{field}.{ext}")
                with open(p, "wb") as fh:
                    fh.write(blob)
                saved.append(p)
        return {"saved": saved, "skipped": skipped}

    # --------------------------------------------------------------- reports
    def reports_for(self, model):
        """The printable reports of a model, as the user sees them in Print."""
        return self.o.search_read(
            "ir.actions.report", [["model", "=", model]],
            ["id", "name", "report_name", "report_type"], context=self.ctx)

    def generate_pdf(self, model, res_id, dest_dir="/tmp"):
        """Produce the PDF of a record and return its path.

        Three facts that make this harder than it looks:

        1. `_render_qweb_pdf` is private — Odoo refuses it outright.
        2. `report_action` returns an action for the UI. Nothing is rendered.
        3. `GET /report/pdf/<name>/<id>` needs a SESSION COOKIE.
           `/web/session/authenticate` rejects API keys with AccessDenied,
           so with key-only access that route is closed.

        What works: the model's own send/print wizard renders the PDF and
        stores it. For invoices it lands in `invoice_pdf_report_file`, not in
        `ir.attachment` — which is why looking only at attachments finds
        nothing and makes you think it failed.
        """
        wizards = {
            "account.move": ("account.move.send.wizard", "action_send_and_print",
                             "invoice_pdf_report_file"),
        }
        existing = self.attachments(model, res_id, "application/pdf")
        if existing:
            name, blob = self.read_binary(existing[0]["id"])
            p = os.path.join(dest_dir, re.sub(r"[/\\]", "_", name))
            open(p, "wb").write(blob)
            return p

        if model in wizards:
            wm, meth, field = wizards[model]
            # already rendered on a previous print?
            try:
                blob = self.field_binary(model, res_id, field)
                return self._save(blob, model, res_id, dest_dir)
            except OdooError:
                pass
            wctx = dict(self.ctx, active_model=model, active_ids=[res_id],
                        active_id=res_id)
            try:
                wid = self.o.call(wm, "create", [{}], {"context": wctx})
                wid = wid[0] if isinstance(wid, list) else wid
                self.o.call(wm, meth, [[wid]], {"context": wctx})
            except (OdooError, OdooExecutedButUnserializable):
                pass                      # verify by reading, not by the return
            blob = self.field_binary(model, res_id, field)
            return self._save(blob, model, res_id, dest_dir)

        raise OdooError(
            f"No known print wizard for {model}. Reports available: "
            f"{[r['report_name'] for r in self.reports_for(model)]}. "
            f"Render them from the UI, or check the model's binary fields: "
            f"{self.binary_fields(model)}")

    def _save(self, blob, model, res_id, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        p = os.path.join(dest_dir, f"{model.replace('.', '_')}_{res_id}.pdf")
        with open(p, "wb") as fh:
            fh.write(blob)
        return p

    # --------------------------------------------------------- notifications
    def audience(self, model, res_id):
        """WHO would receive a message on this record — before you send one.

        Call this before any `mt_comment`. On the reference instance, 12 of
        12 sampled sales orders had an external follower: a comment on any of
        them emails a real customer. The chatter looks internal and is not.
        """
        fol = self.o.search_read(
            "mail.followers",
            [["res_model", "=", model], ["res_id", "=", res_id]],
            ["partner_id"], context=self.ctx)
        pids = [f["partner_id"][0] for f in fol]
        if not pids:
            return {"followers": [], "internal": [], "external": []}
        staff = self.o.search_read(
            "res.users", [["partner_id", "in", pids], ["share", "=", False]],
            ["partner_id"], context=self.ctx)
        ipids = {u["partner_id"][0] for u in staff}
        return {
            "followers": [f["partner_id"][1] for f in fol],
            "internal": [f["partner_id"][1] for f in fol
                         if f["partner_id"][0] in ipids],
            "external": [f["partner_id"][1] for f in fol
                         if f["partner_id"][0] not in ipids],
        }

    def tell(self, model, res_id, body, users, subject=None):
        """Notify colleagues WITHOUT touching followers or emailing customers.

        This is the safe default for "let someone know". It uses
        `message_notify`, which:
          - reaches exactly the partners you name,
          - does NOT subscribe them as followers,
          - does NOT notify the record's other followers,
          - so a customer following the order hears nothing.

        Verified: followers 3 before, 3 after; one inbox notification to the
        one colleague named.
        """
        partner_ids = self._partners_of(users)
        before = self._msg_count(model, res_id)
        try:
            self.o.call(model, "message_notify", [[res_id]],
                        {"partner_ids": partner_ids, "body": body,
                         "subject": subject or "Notification",
                         "context": self.ctx})
        except OdooExecutedButUnserializable:
            pass
        return self._delivery_report(model, res_id, partner_ids, before)

    def _partners_of(self, users):
        if not users:
            return []
        rows = self.o.search_read("res.users", [["id", "in", list(users)]],
                                  ["partner_id"], context=self.ctx)
        return [r["partner_id"][0] for r in rows if r.get("partner_id")]

    def _msg_count(self, model, res_id):
        return self.o.search_count(
            "mail.message", [["model", "=", model], ["res_id", "=", res_id]],
            self.ctx)

    def _delivery_report(self, model, res_id, asked, before):
        """What was actually delivered, per channel — never assume."""
        after = self._msg_count(model, res_id)
        last = self.o.search_read(
            "mail.message", [["model", "=", model], ["res_id", "=", res_id]],
            ["id", "subtype_id", "message_type"], limit=1, order="id desc",
            context=self.ctx)
        notified, mails, reached = [], [], set()
        if last:
            rows = self.o.search_read(
                "mail.notification", [["mail_message_id", "=", last[0]["id"]]],
                ["res_partner_id", "notification_type", "notification_status"],
                context=self.ctx)
            notified = [{"partner": r["res_partner_id"][1],
                         "via": r["notification_type"],
                         "status": r["notification_status"]} for r in rows]
            reached = {r["res_partner_id"][0] for r in rows}
            mails = self.o.search_read(
                "mail.mail", [["mail_message_id", "=", last[0]["id"]]],
                ["state", "email_to", "failure_reason"], context=self.ctx)
        missed = sorted(set(asked) - reached)
        return {
            "posted": after > before,
            "subtype": last[0]["subtype_id"][1] if last else None,
            "notified": notified,
            "emails_generated": [{"state": m["state"],
                                  "to": m.get("email_to"),
                                  "error": m.get("failure_reason")}
                                 for m in mails],
            "not_notified": missed,
            "note": ("not_notified usually contains the message author: Odoo "
                     "never notifies you of your own message")
                    if missed else "",
        }

    def notify(self, model, res_id, body, users=None, subject=None,
               subtype="mail.mt_note"):
        """Post to the chatter. Read `subtype` carefully before using this.

        | subtype           | who sees it                | emails customers? |
        |-------------------|----------------------------|-------------------|
        | mail.mt_note      | internal users only        | no                |
        | mail.mt_comment   | followers, incl. customers | **YES**           |

        Measured on a real order: an `mt_comment` produced two notifications
        — one inbox to the colleague, one **email to the customer partner** —
        without anyone asking for it. `mt_note` on the same record produced
        one inbox notification and zero emails.

        If you only want to reach colleagues, use `tell()` instead: it does
        not involve followers at all.
        """
        partner_ids = self._partners_of(users)
        vals = {"body": body, "subtype_xmlid": subtype,
                "message_type": "comment"}
        if subject:
            vals["subject"] = subject
        if partner_ids:
            vals["partner_ids"] = partner_ids

        before = self._msg_count(model, res_id)
        try:
            self.o.call(model, "message_post", [[res_id]],
                        dict(vals, context=self.ctx))
        except OdooExecutedButUnserializable:
            pass          # message_post returns a mail.message recordset
        return self._delivery_report(model, res_id, partner_ids, before)

    def mail_works(self):
        """Can this instance actually send email? Check BEFORE promising it.

        A dev database usually ships with a neutralised mail server — host
        `invalid`, port 1025 — so everything is 'sent' into a void. Saying
        'the customer has been notified' there is false.
        """
        srv = self.o.search_read(
            "ir.mail_server", [], ["name", "smtp_host", "smtp_port", "active"],
            context=self.ctx)
        neutralised = any(
            (s.get("smtp_host") or "") in ("invalid", "localhost")
            or s.get("smtp_port") == 1025 for s in srv)
        queue = {st: self.o.search_count("mail.mail", [["state", "=", st]],
                                         self.ctx)
                 for st in ("outgoing", "sent", "exception")}
        return {
            "servers": [f"{s['name']} ({s.get('smtp_host')}:{s.get('smtp_port')})"
                        for s in srv],
            "neutralised": neutralised,
            "queue": queue,
            "verdict": ("Email is NEUTRALISED on this instance — nothing "
                        "leaves. Do not tell anyone they were emailed."
                        if neutralised or not srv else
                        "A real SMTP server is configured."),
        }

    def schedule(self, model, res_id, summary, user_id, days=0,
                 note=None, activity_type=None):
        """DEPRECATED — use `Collab.todo()`.

        Kept so existing callers keep working. Activities belong to the
        collaboration layer, which also knows how to complete, postpone and
        reassign them; this could only create one.
        """
        from collaboration import Collab
        c = Collab(self.o)
        c.ctx = self.ctx
        return c.todo(model, res_id, summary, user_id, days=days, note=note)

    def followers(self, model, res_id):
        """Who currently receives messages on this record."""
        return self.o.search_read(
            "mail.followers",
            [["res_model", "=", model], ["res_id", "=", res_id]],
            ["partner_id"], context=self.ctx)

    def chatter(self, model, res_id, limit=10):
        """DEPRECATED — use `Collab.history()`. Same query, one home."""
        from collaboration import Collab
        c = Collab(self.o)
        c.ctx = self.ctx
        return c.history(model, res_id, limit=limit)


if __name__ == "__main__":
    o = connect()
    d = Documents(o, company_id=2)
    inv = o.search_read("account.move",
                        [["move_type", "=", "out_invoice"],
                         ["state", "=", "posted"]],
                        ["id", "name"], limit=1, context=d.ctx)[0]
    print(f"Invoice {inv['name']} (id={inv['id']})")
    print("  binary fields:", d.binary_fields("account.move")[:5])
    print("  reports:", [r["name"] for r in d.reports_for("account.move")])
    p = d.generate_pdf("account.move", inv["id"])
    print(f"  PDF -> {p} ({os.path.getsize(p)} bytes)")
    print("  chatter messages:", len(d.chatter("account.move", inv["id"])))
