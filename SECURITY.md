# Security and deployment

CampusBridge ships fictional demo accounts for local loopback use. The single-process SQLite application is intended for development, demonstrations and reproducible evaluation.

For a hosted deployment, configure production identities, TLS, access restrictions, data retention and authorized campus knowledge before accepting student records. Disable the public demo identities and use an appropriate secret store. Model credentials belong only to the server environment; never use a frontend VITE variable for an API key.

The server validates student ownership, counselor campus scope, case ownership, expected versions and idempotency. Reviewed text and citations are bound before delivery. Risk decisions and case records are persisted independently of model generation.

Report a suspected issue privately to the repository maintainer through an available private security/contact channel. Include a synthetic reproducer and affected commit, and omit credentials and student data. A public issue can describe a general bug once disclosure is coordinated.

Historical experiments use synthetic inputs. Private databases, local logs, credentials, source snapshots and workstation paths stay outside the public export.
