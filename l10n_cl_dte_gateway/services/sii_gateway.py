# Copyright 2026 Fernando San Martin Woerner <fsanmartin@iternova.cl>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


class SiiGateway:
    """Outbound port for SII transport implementations.

    Deployments provide a transport adapter implementing this contract. The
    core workflow never makes network calls itself, so tests can replace this
    port with a deterministic black-box implementation.
    """

    def send_document(self, company, environment, signed_envelope):
        """Return raw bytes, response metadata, TrackID and accepted status.

        The adapter returns a mapping containing ``raw_response`` (bytes),
        ``track_id`` (string), and ``state`` (submitted or outcome_unknown).
        Transport exceptions must expose ``outcome_unknown`` when the remote
        outcome cannot be determined safely.
        """
        raise NotImplementedError("No SII transport adapter is configured.")

    def query_status(self, company, environment, track_id):
        """Return raw status bytes and one recognized fiscal state."""
        raise NotImplementedError("No SII status adapter is configured.")

    def reconcile_submission(self, company, environment, identity):
        """Look up a potentially accepted send without sending the DTE again.

        ``identity`` includes the local idempotency key, DTE type and folio.
        An adapter may return a known receipt/TrackID or a definitive not-found
        result. An ambiguous lookup must raise with ``outcome_unknown = True``.
        """
        raise NotImplementedError("No SII reconciliation adapter is configured.")
