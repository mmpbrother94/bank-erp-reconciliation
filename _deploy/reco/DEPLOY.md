# Deploying the Reconciliation Portal on sieplindia.co.in/reco

The portal becomes a **route on your existing website**.
No new domain. No DNS change. No new SSL certificate.

When you are done, this is the address:

    https://sieplindia.co.in/reco

Total time: about 15 minutes, most of it waiting for step 5.

---

## Before you start

You need the file **`reco_portal.zip`** and your **cPanel** login for the
`sieplindia.co.in` account.

WHM (port 2087) is the *reseller/root* panel. This app is installed from
**cPanel** (port 2083), which is the panel for one website account:

    https://sieplindia.co.in:2083

If you only have the WHM login: WHM -> **List Accounts** -> click the cPanel
icon next to `sieplindia.co.in`. That logs you straight into cPanel.

Decide two values now and write them down:

| what | example | notes |
|---|---|---|
| The password everyone will type | `Surya@Reco2026` | share this with your testers |
| A secret key | (a long random string) | used to sign login cookies, nobody types it |

A ready-made secret key you can copy:

    89a634dcb4bbe7e0603a49c96a0bd020c71f4607a7b72a65a81ce38a70fb9230

---

## Choosing the folder name (and running next to an app you already have)

**The folder name is yours to pick.** The zip extracts to a folder called
`reco`, but you may rename it to anything: `bankreco`, `bank_reco`, `reco2026`.

Two separate things, and they do **not** have to match (keeping them the same
just avoids confusion):

| | what it is | example |
|---|---|---|
| **Application root** | the folder name on disk | `bankreco` |
| **Application URL** | what people type after the domain | `sieplindia.co.in/bankreco` |

Rules for the name: lowercase letters, digits, `-` or `_`. No spaces.

### You already have another app (e.g. gst2b)

That changes nothing, as long as you keep them apart:

* Put this one in its **own folder**, beside the existing one - never inside it:

      /home/<youruser>/gst2b        <- the app you already deployed
      /home/<youruser>/bankreco     <- this one

* Create a **second, separate** application in Setup Python App. cPanel happily
  runs several Python apps on one account; each gets its own virtualenv, so the
  library versions of one cannot break the other.
* Give it a **different Application URL** - `sieplindia.co.in/bankreco` while
  gst2b keeps `sieplindia.co.in/gst2b`.
* Give it its **own environment variables**. They are per application, so
  `RECO_PASSWORD` here does not touch anything in gst2b.

Do **not** extract this zip inside the gst2b folder and do **not** reuse the
gst2b application entry - the startup file and entry point are different and
you would break the working app.

### If you rename the folder

Use the new name in **both** boxes in STEP 2, and if you ever need the
`RECO_URL_PREFIX` fallback in troubleshooting, set it to `/yournewname`.
Nothing inside the code refers to the folder name, so nothing else changes.

---

## STEP 1 — Upload the zip

1. In cPanel, open **File Manager**.
2. In the left tree click the **home directory** — the folder that *contains*
   `public_html`. The path box should read `/home/<yourcpaneluser>`.

   > **Do not go inside `public_html`.** Uploaded bank statements are stored
   > next to the code, and inside `public_html` they would be downloadable by
   > anyone who guesses the filename.

3. Click **Upload**, choose `reco_portal.zip`, wait for 100%.
4. Click **Go Back to ...**, then right-click `reco_portal.zip` -> **Extract**
   -> **Extract Files**.
5. You should now see a folder called **`reco`**. Open it and check these are
   inside:

       webapp.py
       passenger_wsgi.py
       requirements.txt
       reco_system/   (a folder)

6. Delete `reco_portal.zip` — it is no longer needed.

**Note the exact path**, you need it in step 2. It is:

    /home/<yourcpaneluser>/reco

---

## STEP 2 — Create the Python application

1. In cPanel, open **Setup Python App**
   (sometimes listed under *Software* as "Application Manager").
2. Click **CREATE APPLICATION**.
3. Fill in **exactly** this:

   | Field | Value |
   |---|---|
   | Python version | `3.9` or higher — pick the newest offered |
   | Application root | `reco` |
   | Application URL | choose `sieplindia.co.in` from the dropdown, then type `reco` in the box next to it |
   | Application startup file | `passenger_wsgi.py` |
   | Application Entry point | `application` |
   | Passenger log file | leave blank |

   The finished Application URL must read **`sieplindia.co.in/reco`**.

4. Click **CREATE**.

The page now shows a command at the top starting with `source /home/...`.
**Copy that line into notepad** — step 5 may need it.

---

## STEP 3 — Set the three environment variables

Still on the application's page, find **Environment variables** and click
**ADD VARIABLE** three times:

| Name | Value |
|---|---|
| `RECO_PASSWORD` | the password you decided above, e.g. `Surya@Reco2026` |
| `RECO_SECRET_KEY` | the long random string above |
| `RECO_HTTPS_ONLY` | `1` |

Click **SAVE** after each one.

> **`RECO_PASSWORD` is not optional.** Without it the app invents a new random
> password every time it restarts and nobody can log in.

Do this **before** the first start.

---

## STEP 4 — Install the libraries

On the same page, under **Configuration files**:

1. Type `requirements.txt` in the box and click the **+** (add) button.
2. Click **RUN PIP INSTALL**.
3. Wait. It takes **3 to 8 minutes** — pandas is a large library. The page
   shows a spinner; do not close it.
4. When it finishes you should see a success message listing the installed
   packages.

**If pip fails with a memory error**, open **Terminal** in cPanel (or SSH) and
run the `source /home/...` line you copied in step 2, then:

    pip install --no-cache-dir -r requirements.txt

The `--no-cache-dir` flag is what gets pandas through a small-memory account.

---

## STEP 5 — Restart and open it

1. Back at the top of the application page, click **RESTART**.
2. Open a browser and go to:

       https://sieplindia.co.in/reco

3. You should see the **Sign in** page. Enter your `RECO_PASSWORD`.
4. You are on the upload page.

**Done.** Share the link and the password with whoever needs to test.

---

## STEP 6 — Test it once yourself

1. **ERP Payment Import file** -> select your ERP export.
2. **Bank statements** -> select all of them together (Ctrl+A in the picker).
3. Leave the three tolerance boxes as they are.
4. Click **Run reconciliation**.
5. The progress page refreshes itself. A 14,000-row ERP export takes
   **2 to 5 minutes** on shared hosting.
6. The dashboard opens by itself. Check that:
   * the **⌄ Rules & Parameters** button top-right opens the parameter panel;
   * a bank row's **3-sheet Excel** button downloads a file;
   * **Download full Excel workbook** works.

---

## If something goes wrong

| What you see | What it means | Fix |
|---|---|---|
| **500 Internal Server Error**, or a Passenger error page | a library did not install | redo STEP 4, then RESTART |
| The page loads but every link goes to `sieplindia.co.in/` instead of `/reco` | the app cannot see its mount point | add a 4th environment variable `RECO_URL_PREFIX` = `/reco`, then RESTART |
| **404 Not Found** at `/reco` | Application URL was typed wrong | Setup Python App -> edit the app -> Application URL must be `sieplindia.co.in/reco` |
| **"Request Entity Too Large"** when uploading | the ERP file is over the limit | open `reco/webapp.py`, change `MAX_MB = 80` to `MAX_MB = 200`, RESTART |
| Sign-in page reappears after entering the correct password | `RECO_SECRET_KEY` is missing, or `RECO_HTTPS_ONLY=1` while opening the site over plain `http://` | set both variables, use `https://`, RESTART |
| The run sits on the progress page for over 15 minutes | the account ran out of memory | try fewer bank files at a time, or ask your host to raise the memory limit |

To read the real error: **Setup Python App** -> your app -> the **log file**
link, or **File Manager** -> `~/logs/`.

---

## Housekeeping — please read

Every run is saved in `reco/jobs/<timestamp>/` and **that folder contains the
uploaded bank statements**. Delete old run folders from File Manager every few
weeks.

`jobs/` sits outside `public_html`, so it is not reachable from the web at all,
and the login guards every download route. Do not put an `.htaccess` of your own
in the application root - cPanel writes its Passenger configuration there and a
pre-existing file stops the app from ever starting.

To change the password later: Setup Python App -> edit `RECO_PASSWORD` ->
**RESTART**. Everyone is signed out and must use the new one.

---

## Updating to a newer version later

1. File Manager -> upload the new `reco_portal.zip` to the home directory.
2. Extract it, choosing **Replace** when asked about existing files.
3. Setup Python App -> **RESTART**.

Your environment variables and everything under `reco/jobs/` survive the update.
