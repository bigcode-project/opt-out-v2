# Opt-out process for The Stack

You can opt out your code from [The Stack dataset](https://huggingface.co/datasets/HuggingFaceCode/stack-v3-train)
by opening an issue in this repository. Opting out excludes your code from the
next iteration of The Stack and from current and future model training.

The easiest way is the **[Am I in The Stack](https://huggingface.co/spaces/HuggingFaceCode/in-the-stack)**
app: enter your GitHub username (or an organization name), see what's in the
dataset, pick what to remove, and it generates a pre-filled opt-out issue for you.

**Link to opt-out issue:** [click here](https://github.com/bigcode-project/opt-out-v2/issues/new?template=opt-out-request.yml&title=Opt-out+request).

## What you can request

The opt-out form has two lists — fill in **at least one**:

- **Accounts and organizations to remove entirely** — one GitHub username or
  organization per line. Everything under each, now and in the future, is removed.
  List as many as you like (personal accounts, previous usernames, and orgs).
- **Specific repositories to remove** — one `owner/repo` per line. Only those are
  removed; everything else under that owner stays in the dataset. Use this for a
  partial opt-out.

Telling us your relationship to what you listed (owner, maintainer, contributor)
and adding a proof link are **optional** — if there's no easy public way to show
you control an account, that's fine; we'll follow up if we need to check.
