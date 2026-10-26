# Verify Your Emails

A Python script that checks a list of bounced or undeliverable email addresses, fixes common domain typos, and flags addresses that can't receive mail.

## What it does

For each address in a CSV file, the script:

1. Cleans the address (trims spaces and stray punctuation, lowercases it).
2. Looks up the domain's mail servers (MX records).
3. If the domain has none, tries to correct common typos (for example `chartern.net` to `charter.net`).
4. Optionally (`--smtp`) asks the mail server whether the mailbox exists, without sending any email.
5. Optionally searches for alternative addresses from first and last names (requires `--smtp`).

## Requirements

- Python 3.8 or newer
- [dnspython](https://www.dnspython.org/)

## Setup

```
python -m venv venv
venv\Scripts\activate        # Windows (Command Prompt)
pip install dnspython
```

On macOS or Linux, activate with `source venv/bin/activate`.

## Usage

Put your list in the project folder as a CSV with a header row and a column named `email`.

Run the basic check:

```
python app.py email-errors.csv email-results.csv
```

SMTP example:

```
python app.py email-errors.csv email-smtp-results.csv --smtp --helo yourdomain.com --mail-from verify@yourdomain.com
```

## Output

The results file contains your original columns plus `status`, `checked_email`, `suggested_email` and `notes`.

Possible statuses:

- `valid`: the mailbox accepted the check (SMTP mode)
- `found_alternative`: a different address matched (SMTP mode, needs name columns)
- `mx_ok_mailbox_unverified`: the domain accepts mail, but the mailbox wasn't checked
- `domain_has_no_mail_server`: the domain can't receive mail
- `risky_catch_all`: the domain accepts any address, so the mailbox can't be confirmed
- `undeliverable`: the mailbox was rejected
- `unknown`: the server couldn't be reached or didn't give a clear answer
- `invalid_syntax`: the address couldn't be parsed

## Limitations

- Gmail, Yahoo, Outlook, AOL, Comcast and similar providers can't be mailbox-checked, so only their domains are verified.
- Many home internet providers and cloud hosts block outbound port 25. In that case SMTP mode returns mostly `unknown`.
- `--helo` and `--mail-from` should use a domain you own. Don't use someone else's domain.
- A suggested address is a best guess, not a guarantee.
- Don't run SMTP checks on large lists from an IP address you care about. For big lists, use a dedicated verification service.

## Privacy

Don't commit real email lists to a public repository. Keep CSV files that contain personal data out of version control.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.