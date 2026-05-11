# Snowy Mountains Accommodation — Homhero to Meta Feed

Prepared by **Manus AI**.

This package lets GitHub automatically create a Meta Commerce Manager catalogue CSV from Homhero listings. It is designed so you do **not** need to run Python manually each day. GitHub Actions runs the script on a schedule, keeps the Homhero API key private, and publishes the CSV through GitHub Pages.

## What this setup does

The setup fetches your listings from Homhero, converts them into the product catalogue format Meta accepted after the previous error report, and publishes the finished file at a stable public URL. Meta Commerce Manager can then fetch that URL daily or several times per day.

| Item | Value |
| --- | --- |
| Main script | `generate_meta_feed.py` |
| Scheduled workflow | `.github/workflows/update-meta-feed.yml` |
| Published feed file | `docs/snowy_mountains_meta_products.csv` |
| Diagnostics file | `docs/snowy_mountains_meta_products_diagnostics.csv` |
| Private secret needed | `HOMHERO_API_KEY` |
| Optional variable for currency mismatch | `META_FEED_CURRENCY` |
| Optional default fallback from-price | `META_FEED_FALLBACK_PRICE` |
| Optional Homhero API starting-price switch | `USE_HOMHERO_STARTING_PRICE` |
| Optional website from-price switch | `USE_WEBSITE_FROM_PRICE` |
| Optional manual per-property price overrides | `META_FEED_PRICE_OVERRIDES` |
| Default run frequency | Four times per day |

## Before you start

You need a free GitHub account. You also need the Homhero API key you created earlier. The API key must be saved as a **GitHub secret**, not typed into the Python file, the README, or the public repository.

## Step 1 — Create a new GitHub repository

Go to [GitHub](https://github.com/) and log in. Click the **+** button in the top-right corner, then choose **New repository**. Use a simple name such as `snowy-meta-feed`.

Set the repository to **Public** if you want to use GitHub Pages without extra access restrictions. You can choose **Private**, but public GitHub Pages behaviour depends on your GitHub plan and settings. For this feed, there is no API key in the CSV, so a public repository is usually acceptable.

Do not tick any boxes for adding a README, `.gitignore`, or licence, because this package already includes the files you need. Click **Create repository**.

## Step 2 — Upload these files to GitHub

After creating the repository, GitHub will show an empty repository page. Click **uploading an existing file**. Drag all files and folders from this package into GitHub, including the hidden `.github` folder.

If your computer does not show hidden folders, upload the files using the ZIP package provided by Manus, or use GitHub Desktop. The most important folder is `.github/workflows/`, because that is what makes the scheduled automation run.

When the files are uploaded, scroll down and click **Commit changes**.

Your repository should contain this structure:

```text
snowy-meta-feed/
├── .github/
│   └── workflows/
│       └── update-meta-feed.yml
├── docs/
│   ├── snowy_mountains_meta_products.csv
│   └── snowy_mountains_meta_products_diagnostics.csv
├── generate_meta_feed.py
├── requirements.txt
└── README.md
```

## Step 3 — Add your Homhero API key as a GitHub secret

Open your repository in GitHub. Click **Settings** near the top of the repository. In the left menu, click **Secrets and variables**, then click **Actions**.

Click **New repository secret**. In the **Name** field, type exactly:

```text
HOMHERO_API_KEY
```

In the **Secret** field, paste your Homhero API key. Click **Add secret**.

The script reads the key from this secret when the automation runs. The key is not written into the public CSV and should not be committed into the repository.

## Step 4 — Turn on GitHub Actions if prompted

Click the **Actions** tab in your repository. If GitHub asks whether you want to enable workflows, click the button to enable them.

You should see a workflow named **Update Meta catalogue feed**.

## Step 5 — Run the feed once manually

In the **Actions** tab, click **Update Meta catalogue feed**. Click **Run workflow**, then click the green **Run workflow** button.

Wait a few minutes. When the run finishes, it should show a green tick. If it fails, click the failed run and read the error message. The most common issue is that the secret name was typed incorrectly. It must be exactly `HOMHERO_API_KEY`.

After the workflow succeeds, go back to the **Code** tab. You should see updated files in the `docs` folder.

## Step 6 — Enable GitHub Pages

Click **Settings** in the repository, then click **Pages** in the left menu.

Under **Build and deployment**, set the source to **Deploy from a branch**. For the branch, choose `main`. For the folder, choose `/docs`. Click **Save**.

After a minute or two, GitHub will show a public website URL. It will usually look like this:

```text
https://YOUR-GITHUB-USERNAME.github.io/snowy-meta-feed/
```

Your Meta feed URL will be:

```text
https://YOUR-GITHUB-USERNAME.github.io/snowy-meta-feed/snowy_mountains_meta_products.csv
```

Replace `YOUR-GITHUB-USERNAME` with your GitHub username, and replace `snowy-meta-feed` if you used a different repository name.

## Step 7 — Put the feed URL into Meta Commerce Manager

In Meta Commerce Manager, go to your catalogue. Choose **Data sources**, then add or replace the data feed. Choose the option for a **scheduled feed** or **data feed URL**.

Paste the GitHub Pages CSV URL:

```text
https://YOUR-GITHUB-USERNAME.github.io/snowy-meta-feed/snowy_mountains_meta_products.csv
```

Set Meta to fetch the feed daily. If Meta allows several fetches per day, you can choose that too. The GitHub workflow already refreshes the file four times per day.

## How often does it run?

The workflow currently runs four times per day. The schedule is controlled by this line in `.github/workflows/update-meta-feed.yml`:

```yaml
- cron: "0 20,2,8,14 * * *"
```

GitHub schedules use UTC time. This schedule is intended to roughly cover morning, midday, evening, and overnight updates for Australia/Sydney. It does not perfectly follow daylight saving time, so the local run time can shift by one hour during daylight saving.

## How to change the schedule

If you want the feed to run once per day instead, change the cron line to:

```yaml
- cron: "0 20 * * *"
```

If you want it to run every six hours, use:

```yaml
- cron: "0 */6 * * *"
```

For most accommodation catalogue use, every six hours is enough. Meta is unlikely to need minute-by-minute updates unless you are pushing live room-level pricing and availability.

## Important notes

The generated feed now uses a **pricing hierarchy** so Meta no longer sees the misleading `1.00 AUD` placeholder. First, the script tries to use a Homhero API starting price where Homhero provides a supported positive nightly, minimum, base, or from-price field. If the API does not expose a usable price, the feed can use optional manual per-property overrides from `META_FEED_PRICE_OVERRIDES`. If there is no override, the script reads the public listing page and looks for the website wording such as `From $308 /night based on a 7 night stay`. This lets the Meta feed vary by property when the public website publishes a from-price.

If none of those sources provides a usable price, the final safety net is `META_FEED_FALLBACK_PRICE`, which defaults to `308.00` in the workflow. Meta's `price` column must still be a numeric value plus a three-letter currency code, for example `308.00 AUD`; the words “Rates from” are added to the description instead of being put inside the price field.

The description now uses wording like `Rates from AU$308 per night. Final price depends on dates, guests and availability.` This makes clear that the Meta catalogue price is a starting rate, not a guaranteed quote for every date range.

Meta's own product template expects the `price` field to contain both the amount and the three-letter ISO currency code, for example `10.00 USD` or `308.00 AUD`. This package builds that value from the detected starting price, or from two fallback settings: `META_FEED_FALLBACK_PRICE`, which is set to `308.00` in the workflow, and `META_FEED_CURRENCY`, which defaults to `AUD`.

The generated feed uses `999` as the quantity because Meta required a positive `quantity_to_sell_on_facebook` value in the error report. This value is a catalogue compliance placeholder, not a real stock count.

## How to change the default fallback from-price

Most listings should use either a Homhero API price, a manual override, or the public website's own `From $X /night based on a 7 night stay` price. The default fallback is only used when none of those sources provides a usable price. It is currently set to `308.00`, based on the Tanderra 10 example showing `From $308 /night based on a 7 night stay`.

To change the default fallback without editing Python, open the repository in GitHub, then go to **Settings → Secrets and variables → Actions → Variables → New repository variable**. In **Name**, type exactly:

```text
META_FEED_FALLBACK_PRICE
```

In **Value**, type the amount only, without the dollar sign or currency code. For example:

```text
308.00
```

Click **Add variable**, then go to **Actions → Update Meta catalogue feed → Run workflow**. After the workflow finishes, open `docs/snowy_mountains_meta_products_diagnostics.csv` and check the `pricing_note` column.

## Optional manual per-property overrides

Manual overrides are not needed for normal use, because the script tries Homhero first and then the public website page. If you ever need to force a specific property to use a specific from-price, you can add a GitHub repository variable named `META_FEED_PRICE_OVERRIDES`.

The value must be a small JSON object. You can use a listing ID, slug, or title as the key. For example, this would force Tanderra 10 on Park Road to use `308.00`:

```json
{"102":"308.00","tanderra-10-on-park-road":"308.00"}
```

After saving the variable, run the workflow again. If the override is used, the diagnostics file will show a `pricing_note` beginning with `manual_price_override:`.

## If Meta reports “Item currency and shopfront dominant currency mismatch”

This error usually means that the currency in the feed is valid, but it does not match the currency Meta has assigned to the shopfront or sales channel. For this Snowy Mountains Accommodation feed, the CSV is currently using `AUD`, which is the expected Australian currency. If Commerce Manager says the shopfront dominant currency is something else, change the feed currency in GitHub rather than editing the CSV manually.

To set the currency in GitHub, open the repository, then go to **Settings → Secrets and variables → Actions → Variables → New repository variable**. In **Name**, type exactly:

```text
META_FEED_CURRENCY
```

In **Value**, type the three-letter currency code shown by Meta for the shopfront, for example:

```text
AUD
```

Click **Add variable**, then go to **Actions → Update Meta catalogue feed → Run workflow**. After the workflow finishes, open `docs/snowy_mountains_meta_products.csv` and confirm that the `price` values use the same currency code as the Meta shopfront.

## Troubleshooting

| Problem | Likely cause | Fix |
| --- | --- | --- |
| GitHub Action fails with `Missing HOMHERO_API_KEY` | The secret is missing or named incorrectly | Add a repository secret named exactly `HOMHERO_API_KEY`. |
| The feed URL gives a 404 error | GitHub Pages is not enabled or has not finished publishing | Go to **Settings → Pages**, select branch `main` and folder `/docs`, then wait a few minutes. |
| Meta still asks you to map fields | Meta is reviewing the CSV headers | Map `id` to `id`, `title` to `title`, `price` to `price`, `link` to `link`, `availability` to `availability`, and `condition` to `condition`. |
| Meta reports price warnings | Meta may need to refresh its data source, or a listing may be using the default fallback because neither Homhero nor the public page exposed a usable price | Open `docs/snowy_mountains_meta_products_diagnostics.csv` and check the `pricing_note` column. `api_starting_price:...` means a Homhero API price was used; `website_from_price_7_night` means the public website's 7-night from-price was used; `manual_price_override:...` means a manual override was used; `default_fallback_from_price:...` means the final safety-net price was used. |
| Meta reports `Item currency and shopfront dominant currency mismatch` | The feed currency does not match the Commerce Manager shopfront currency | Check the shopfront currency in Meta. If it is not `AUD`, add or update the GitHub repository variable `META_FEED_CURRENCY` to match Meta exactly, then rerun the workflow. |
| Listings are missing images | Homhero did not return a usable image URL for that listing | Check `docs/snowy_mountains_meta_products_diagnostics.csv`. |

## Files Meta should use

Meta should use this file only:

```text
docs/snowy_mountains_meta_products.csv
```

The diagnostics file is only for checking issues. Do not upload the diagnostics file to Meta.
