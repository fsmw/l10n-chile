# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import email
import email.policy
import logging

from lxml import etree
from odoo import _, api, fields, models
from odoo.addons.l10n_cl_dte_core.tools.xml_safety import safe_fromstring
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Read at most this many new messages per run so a large mailbox cannot time out.
DTE_FETCH_BATCH = 50
DTE_ROOTS = frozenset({"EnvioDTE", "DTE", "EnvioBOLETA"})


def _is_dte_xml(content):
    """Recognize DTE documents; SII answers and other XML are not imported."""
    try:
        return etree.QName(safe_fromstring(content)).localname in DTE_ROOTS
    except (etree.XMLSyntaxError, ValueError):
        return False


class FetchmailServer(models.Model):
    _inherit = "fetchmail.server"

    l10n_cl_dte_intake = fields.Boolean(
        string="Chilean DTE Intake",
        help="Import the XML attachments of new messages as incoming DTEs.",
    )
    l10n_cl_dte_company_id = fields.Many2one(
        "res.company",
        string="DTE Receiver Company",
        help="Company that receives the DTEs sent to this mailbox.",
    )
    l10n_cl_dte_user_id = fields.Many2one(
        "res.users",
        string="DTE Import User",
        help="DTE manager recorded as the importer of the received DTEs.",
    )
    l10n_cl_dte_last_uid = fields.Integer(
        string="Last DTE Message UID",
        readonly=True,
        help="IMAP UID of the last message read for DTE intake.",
    )

    @api.constrains(
        "l10n_cl_dte_intake",
        "server_type",
        "l10n_cl_dte_company_id",
        "l10n_cl_dte_user_id",
    )
    def _check_dte_intake(self):
        for server in self.filtered("l10n_cl_dte_intake"):
            if server.server_type != "imap":
                raise ValidationError(_("DTE intake needs an IMAP server."))
            user = server.l10n_cl_dte_user_id
            company = server.l10n_cl_dte_company_id
            if not company or not user:
                raise ValidationError(
                    _("DTE intake needs a receiver company and an import user.")
                )
            if company not in user.company_ids or not user.has_group(
                "l10n_cl_dte_core.group_dte_manager"
            ):
                raise ValidationError(
                    _("The DTE import user must be a DTE manager of the company.")
                )

    def fetch_mail(self, raise_exception=True):
        intake = self.filtered("l10n_cl_dte_intake")
        for server in intake:
            server._fetch_dte_mail(raise_exception)
        return super(FetchmailServer, self - intake).fetch_mail(raise_exception)

    def _fetch_dte_mail(self, raise_exception):
        """Import the DTE attachments of messages newer than the last UID."""
        self.ensure_one()
        imap = None
        last_uid = self.l10n_cl_dte_last_uid
        try:
            imap = self.connect()
            imap.select()
            _status, data = imap.uid("search", None, f"(UID {last_uid + 1}:*)")
            uids = sorted(int(uid) for uid in data[0].split() if int(uid) > last_uid)
            for uid in uids[:DTE_FETCH_BATCH]:
                # BODY.PEEK keeps the message unread for the people using the box.
                _status, fetched = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
                if fetched and isinstance(fetched[0], tuple):
                    self._import_dte_message(fetched[0][1])
                last_uid = uid
        except Exception as error:
            if raise_exception:
                raise UserError(
                    _("Could not read DTE messages from %(server)s: %(error)s")
                    % {"server": self.name, "error": error}
                ) from error
            _logger.warning("DTE intake failed on server %s", self.name, exc_info=True)
        finally:
            if imap:
                try:
                    imap.close()
                    imap.logout()
                except Exception:  # pylint: disable=broad-except
                    _logger.warning("Could not close IMAP server %s", self.name)
            self.write(
                {"l10n_cl_dte_last_uid": last_uid, "date": fields.Datetime.now()}
            )
        return True

    def _import_dte_message(self, raw_message):
        """Import every XML attachment; other XML, such as SII answers, is skipped."""
        self.ensure_one()
        message = email.message_from_bytes(raw_message, policy=email.policy.SMTP)
        importer = (
            self.env["l10n_cl.incoming.dte.import"]
            .with_user(self.l10n_cl_dte_user_id)
            .with_company(self.l10n_cl_dte_company_id)
        )
        imported = importer.browse()
        for part in message.walk():
            filename = part.get_filename() or ""
            if not filename.lower().endswith(".xml"):
                continue
            content = part.get_payload(decode=True)
            if not content or not _is_dte_xml(content):
                continue
            try:
                with self.env.cr.savepoint():
                    imported |= importer._import_xml(content, "email", filename)
            except (UserError, ValidationError) as error:
                _logger.info("Skipped attachment %s: %s", filename, error)
        return imported
