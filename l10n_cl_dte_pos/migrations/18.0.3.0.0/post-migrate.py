# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


def migrate(cr, version):
    """Preserve the previous voucher prompt behavior on upgraded databases."""
    cr.execute(
        """
        UPDATE pos_payment_method AS method
           SET is_card_payment = (journal.type = 'bank')
          FROM account_journal AS journal
         WHERE journal.id = method.journal_id
        """
    )
