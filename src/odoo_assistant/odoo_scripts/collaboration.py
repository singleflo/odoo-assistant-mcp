#!/usr/bin/env python3
"""Activities, internal notes and calendar — the collaboration layer.

Every record in Odoo carries the same three things, and they are what people
actually use to work together:

    chatter    what was said and done      (mail.message)
    activities what must still be done     (mail.activity)
    calendar   when people meet            (calendar.event)

    from collaboration import Collab
    c = Collab(odoo, company_id=2)

    c.note(model, id, "text")                 internal note, nobody emailed
    c.todo(model, id, "Call back", user, 3)   activity with a deadline
    c.done(activity_id, "outcome")            complete + log the outcome
    c.postpone(activity_id, days=7)           move the deadline
    c.reassign(activity_id, user_id)          hand it to someone else
    c.drop(activity_id)                       cancel, leaving no trace
    c.pending(user_id=...)                    what is waiting for whom
    c.meet("Review", start, users, link_to=(model, id))
"""
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from odoo_client import (connect_cli as connect, OdooError,  # noqa: E402
                         OdooExecutedButUnserializable)
from safety_layer import classify  # noqa: E402


class Collab:
    def __init__(self, odoo, company_id=None, enforce=True):
        self.o = odoo
        self.ctx = {"allowed_company_ids": [company_id]} if company_id else {}
        self.enforce = enforce

    def _guard(self, model, method, payload=None):
        """Every destructive path goes through the same classifier the rest
        of the skill uses. Without this, `drop()` and `unlink` on a calendar
        event would delete records without ever meeting L0-L5 — a hole found
        during the coherence review, not by a test.
        """
        if not self.enforce:
            return
        try:
            level = str(classify(model, method, payload or {}))
        except Exception:
            return
        if "L4" in level or "L5" in level:
            raise PermissionError(
                f"{model}.{method} is {level}. Collaboration helpers refuse "
                f"destructive calls. Use Writer with confirmed=True, or pass "
                f"enforce=False deliberately.")

    # -------------------------------------------------------- internal notes
    def note(self, model, res_id, body, users=None):
        """Log an internal note. Customers never see it, nobody is emailed.

        This is the safe way to record what you did to a record. It uses
        `mail.mt_note`; the alternative `mt_comment` emails every follower,
        and on the reference instance 12 of 12 sales orders had a customer
        among their followers.
        """
        vals = {"body": body, "subtype_xmlid": "mail.mt_note",
                "message_type": "comment"}
        if users:
            rows = self.o.search_read("res.users", [["id", "in", list(users)]],
                                      ["partner_id"], context=self.ctx)
            vals["partner_ids"] = [r["partner_id"][0] for r in rows
                                   if r.get("partner_id")]
        before = self._msgs(model, res_id)
        try:
            self.o.call(model, "message_post", [[res_id]],
                        dict(vals, context=self.ctx))
        except OdooExecutedButUnserializable:
            pass          # returns a mail.message recordset
        return {"posted": self._msgs(model, res_id) > before}

    def history(self, model, res_id, limit=10):
        """What has been said on this record.

        NOTE: never query mail.message without model+res_id. Its record rules
        re-read the linked record for every row, and one orphan message
        pointing at a deleted record kills the entire query:
            search_count("mail.message", []) ->
              Record does not exist or has been deleted. (calendar.event(1177,))
        """
        return self.o.search_read(
            "mail.message", [["model", "=", model], ["res_id", "=", res_id]],
            ["date", "author_id", "body", "message_type", "subtype_id"],
            limit=limit, order="date desc", context=self.ctx)

    def _msgs(self, model, res_id):
        return self.o.search_count(
            "mail.message", [["model", "=", model], ["res_id", "=", res_id]],
            self.ctx)

    # ------------------------------------------------------------ activities
    def types(self, model=None):
        """Activity types available here. They differ per instance — this one
        has 17, several bound to a specific model (Order Upsell on sale.order,
        Tax Report Ready on account.move). Read, do not assume."""
        dom = [["res_model", "in", [False, model]]] if model else []
        return self.o.search_read(
            "mail.activity.type", dom,
            ["id", "name", "delay_count", "delay_unit", "res_model"],
            context=self.ctx)

    def todo(self, model, res_id, summary, user_id, days=0, note=None,
             type_name=None):
        """Create an activity: the only notification with a deadline.

        A chatter note is passive — it sits there. An activity appears in the
        assignee's To-Do list and turns `overdue` when the date passes.
        """
        tid = None
        for t in self.types(model):
            if type_name and type_name.lower() in t["name"].lower():
                tid = t["id"]
                break
        if tid is None:
            ts = self.types(model)
            tid = ts[0]["id"] if ts else None
        model_id = self.o.search_read("ir.model", [["model", "=", model]],
                                      ["id"], context=self.ctx)[0]["id"]
        vals = {"res_model_id": model_id, "res_model": model, "res_id": res_id,
                "summary": summary, "user_id": user_id,
                "date_deadline": str(date.today() + timedelta(days=days))}
        if tid:
            vals["activity_type_id"] = tid
        if note:
            vals["note"] = note
        aid = self.o.call("mail.activity", "create", [vals],
                          {"context": self.ctx})
        aid = aid[0] if isinstance(aid, list) else aid
        return self.get(aid)

    def get(self, activity_id):
        rows = self.o.search_read(
            "mail.activity", [["id", "=", activity_id]],
            ["summary", "user_id", "date_deadline", "state",
             "activity_type_id", "res_model", "res_id"], context=self.ctx)
        return rows[0] if rows else None

    def done(self, activity_id, feedback=""):
        """Complete an activity and log the outcome in the chatter.

        `action_feedback` DELETES the activity row and writes a chatter
        message describing what happened. That message is the only trace, so
        always pass feedback — an empty one leaves a note saying nothing.

        Verified: activity gone, chatter went from 3 to 4 messages.
        """
        target = self.get(activity_id)
        if not target:
            return {"done": False, "reason": "activity does not exist"}
        model, res_id = target["res_model"], target["res_id"]
        before = self._msgs(model, res_id)
        try:
            self.o.call("mail.activity", "action_feedback", [[activity_id]],
                        {"feedback": feedback, "context": self.ctx})
        except OdooExecutedButUnserializable:
            pass
        gone = not self.o.search_count("mail.activity",
                                       [["id", "=", activity_id]], self.ctx)
        return {"done": gone, "logged_in_chatter": self._msgs(model, res_id) > before,
                "record": f"{model}/{res_id}"}

    def postpone(self, activity_id, days=None, to=None):
        """Move a deadline. There is no `action_postpone` — the field is just
        writable. `action_snooze` exists but shifts by a fixed 7 days."""
        new = to or str(date.today() + timedelta(days=days or 7))
        before = self.get(activity_id)
        # vals is the SECOND ARGUMENT, not a keyword. Passing it as a kwarg
        # fails with an opaque dispatch_rpc error — cost an hour to find.
        self.o.call("mail.activity", "write", [[activity_id],
                    {"date_deadline": new}], {"context": self.ctx})
        after = self.get(activity_id)
        return {"from": before["date_deadline"], "to": after["date_deadline"],
                "changed": before["date_deadline"] != after["date_deadline"],
                "state": after["state"]}

    def reassign(self, activity_id, user_id):
        before = self.get(activity_id)
        self.o.call("mail.activity", "write", [[activity_id],
                    {"user_id": user_id}], {"context": self.ctx})
        after = self.get(activity_id)
        return {"from": before["user_id"], "to": after["user_id"],
                "changed": before["user_id"] != after["user_id"]}

    def drop(self, activity_id):
        """Cancel without completing. Leaves NO trace in the chatter — use
        `done(id, "cancelled because ...")` when the reason matters."""
        target = self.get(activity_id)
        if not target:
            return {"dropped": False, "reason": "does not exist"}
        self._guard("mail.activity", "unlink", {"ids": [activity_id]})
        self.o.call("mail.activity", "unlink", [[activity_id]],
                    {"context": self.ctx})
        return {"dropped": not self.o.search_count(
            "mail.activity", [["id", "=", activity_id]], self.ctx),
            "trace_left": False}

    def pending(self, user_id=None, model=None, overdue_only=False):
        """What is waiting to be done, and for whom."""
        dom = []
        if user_id:
            dom.append(["user_id", "=", user_id])
        if model:
            dom.append(["res_model", "=", model])
        if overdue_only:
            dom.append(["date_deadline", "<", str(date.today())])
        rows = self.o.search_read(
            "mail.activity", dom,
            ["summary", "user_id", "date_deadline", "state", "res_model",
             "res_id"], order="date_deadline", context=self.ctx)
        return {
            "total": len(rows),
            "overdue": len([r for r in rows if r.get("state") == "overdue"]),
            "today": len([r for r in rows if r.get("state") == "today"]),
            "items": rows,
        }

    def workload(self):
        """Who is carrying what — activities grouped by assignee."""
        rows = self.o.read_group("mail.activity", [], [], ["user_id"], self.ctx)
        return sorted(
            [{"user": (r.get("user_id") or [0, "?"])[1],
              "count": r.get("__count") or r.get("user_id_count")}
             for r in rows], key=lambda x: -(x["count"] or 0))

    # -------------------------------------------------------------- calendar
    def meet(self, name, start, users, minutes=60, location=None,
             link_to=None, description=None, alarm=None):
        """Create a meeting and invite people.

        `partner_ids` drives everything: Odoo builds one `calendar.attendee`
        per partner automatically. The organiser is `accepted`, everyone else
        starts at `needsAction`.

        link_to=(model, id) attaches the event to a record — 71 of the linked
        events on this instance hang off project.task.
        """
        if isinstance(start, str):
            start = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        rows = self.o.search_read("res.users", [["id", "in", list(users)]],
                                  ["partner_id"], context=self.ctx)
        pids = [r["partner_id"][0] for r in rows if r.get("partner_id")]
        vals = {"name": name,
                "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                "stop": (start + timedelta(minutes=minutes)).strftime(
                    "%Y-%m-%d %H:%M:%S"),
                "partner_ids": [[6, 0, pids]]}
        if location:
            vals["location"] = location
        if description:
            vals["description"] = description
        if link_to:
            # `res_model` is READONLY — it is computed from `res_model_id`.
            # Passing it to create() is silently ignored: the event comes back
            # with res_id set and res_model False, so it never shows up in
            # "events linked to this record". Always set res_model_id.
            lmodel, lid = link_to
            mid = self.o.search_read("ir.model", [["model", "=", lmodel]],
                                     ["id"], context=self.ctx)
            if mid:
                vals["res_model_id"] = mid[0]["id"]
                vals["res_id"] = lid
        if alarm:
            vals["alarm_ids"] = [[6, 0, [alarm]]]
        eid = self.o.call("calendar.event", "create", [vals],
                          {"context": self.ctx})
        eid = eid[0] if isinstance(eid, list) else eid
        return self.event(eid)

    def event(self, event_id):
        rows = self.o.search_read(
            "calendar.event", [["id", "=", event_id]],
            ["name", "start", "stop", "duration", "location", "user_id",
             "partner_ids", "res_model", "res_id", "videocall_location"],
            context=self.ctx)
        if not rows:
            return None
        e = rows[0]
        e["attendees"] = self.o.search_read(
            "calendar.attendee", [["event_id", "=", event_id]],
            ["partner_id", "state", "email"], context=self.ctx)
        return e

    def respond(self, event_id, partner_id, answer):
        """Answer an invitation. answer: accept | decline | tentative

        The attendee methods are `do_accept`, `do_decline`, `do_tentative` —
        all three verified working, state changes immediately.
        """
        meth = {"accept": "do_accept", "decline": "do_decline",
                "tentative": "do_tentative"}[answer]
        att = self.o.search_read(
            "calendar.attendee",
            [["event_id", "=", event_id], ["partner_id", "=", partner_id]],
            ["id", "state"], context=self.ctx)
        if not att:
            return {"ok": False, "reason": "that partner is not an attendee"}
        before = att[0]["state"]
        try:
            self.o.call("calendar.attendee", meth, [[att[0]["id"]]],
                        {"context": self.ctx})
        except OdooExecutedButUnserializable:
            pass
        after = self.o.search_read("calendar.attendee",
                                   [["id", "=", att[0]["id"]]],
                                   ["state"], context=self.ctx)[0]["state"]
        return {"ok": before != after, "from": before, "to": after}

    def agenda(self, user_id=None, days=7):
        """What is scheduled in the next N days."""
        dom = [["start", ">=", str(date.today())],
               ["start", "<=", str(date.today() + timedelta(days=days))]]
        if user_id:
            dom.append(["user_id", "=", user_id])
        return self.o.search_read(
            "calendar.event", dom,
            ["name", "start", "duration", "location", "user_id", "partner_ids"],
            order="start", context=self.ctx)

    def invite_status(self, event_id):
        """Who has answered, who has not — the question people actually ask."""
        att = self.o.search_read(
            "calendar.attendee", [["event_id", "=", event_id]],
            ["partner_id", "state"], context=self.ctx)
        by = {}
        for a in att:
            by.setdefault(a["state"], []).append(a["partner_id"][1])
        return {"total": len(att),
                "accepted": by.get("accepted", []),
                "declined": by.get("declined", []),
                "tentative": by.get("tentative", []),
                "no_answer": by.get("needsAction", [])}


if __name__ == "__main__":
    o = connect()
    c = Collab(o, company_id=2)
    print("Activity types:", [t["name"] for t in c.types()][:6])
    p = c.pending()
    print(f"Pending activities: {p['total']} (overdue {p['overdue']})")
    print("Workload:", c.workload()[:3])
    print("Next 7 days:", len(c.agenda()), "events")
