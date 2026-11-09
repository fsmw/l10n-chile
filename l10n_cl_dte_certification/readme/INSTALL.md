PycURL is required by the Certification CAF acquisition client. Install the
Python package from the repository's root ``requirements.txt``. The Odoo
runtime must use PycURL linked to libcurl 7.71.0 or newer with the OpenSSL TLS
backend and the ``SSLCERT_BLOB`` and ``SSLKEY_BLOB`` options. The client fails
closed when this runtime contract is not met; it never writes certificate or
private-key material to temporary files.
