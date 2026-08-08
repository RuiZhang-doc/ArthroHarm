# Security and use

ArthroHarm is research software that processes local document files. It does not require network access for extraction or scoring.

Do not use untrusted PDF or HTML inputs outside an isolated environment. The PDF workflow invokes a user-supplied `pdftotext` binary path; verify that the binary is from a trusted Poppler installation.

The software has not undergone medical-device, clinical-safety or cybersecurity certification. It must not be used for diagnosis, treatment, patient monitoring or autonomous evidence-review decisions.

