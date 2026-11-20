Configure a signing profile with a valid certificate and its private key for
the SII environment (`l10n_cl_dte_signing`) and the company RUT. The server
needs PycURL built against OpenSSL and libcurl 7.71 or later.

To request folios manually, a DTE manager opens *DTE > Operations > SII CAF
Requests*, creates a request with the environment, DTE type and quantity,
approves it and presses **Request CAF from SII**. On success the new CAF is
validated and linked to the request; the SII response can be downloaded from
the *Evidence* tab.

- A `failed` request that never reached the allocation step and failed for a
  transient reason (connection, session) shows **Retry**.
- An `uncertain` request must not be sent again. Press **Recover CAF from
  SII** to download the CAF of the reserved range and reconcile it. If the
  SII does not list the range, the request stays uncertain with the recovery
  code; import the CAF manually in *CAF Authorizations*, select it in *CAF for
  Reconciliation* and press **Reconcile**.

To replenish automatically, create a rule in *DTE > Configuration > CAF
Replenishment Rules* with the threshold and quantity. The hourly job queues a
pending request when availability is at or below the threshold. Choose
*Automatic Execution by the Scheduled Job* and an *Execute As* DTE manager to
let the second hourly job run those requests without approval.

Production uses the same assistant flow that Certification already uses, on
the palena host. Run one manual Production request and check its CAF before
enabling automatic execution in Production.
