<h1 align="center">FunPay Cardinal</h1>
<h4 align="center">Простой и эффективный бот для автоматизации FunPay</h4>

<h2 align="center">Перед началом настоятельно рекомендую залететь в наш <a href="https://t.me/funpay_cardinal">Telegram
чат (клик)</a>. Тут и поможем чем сможем и посидеть можно.</h2>

## :clipboard: **Содержание**

- [Возможности](#robot-возможности)
    - [FunPay](#shopping_cart-funpay)
    - [Уведомления и ПУ в Telegram](#left_speech_bubble-уведомления-и-пу-в-telegram)
    - [Дополнительные возможности](#gear-дополнительные-возможности)

- [Преимущества](#1st_place_medal-преимущества)
    - [Для пользователей](#grinning-для-пользователей)
    - [Для разработчиков](#computer-для-разработчиков)

- [Плагины](#electric_plug-плагины)
- [Установка](#arrow_down-установка)
    - [Windows](#large_blue_diamond-windows)
    - [Linux (Ubuntu)](#hotsprings-linux-ubuntu)
- [Установка плагинов](#electric_plug-установка-плагинов)
- [Мне нужна помощь](#question-мне-нужна-помощь)
- [Star it](#star-star-it)

## :robot: **Возможности**

### :shopping_cart: **FunPay**

- Автовыдача товаров.
- Автоподнятие лотов.
- Автоответ на заготовленные команды.
- Автовосстановление лотов после продажи.
- Автодеактивация лотов, если товары закончились.
- Вечный онлайн.
- Уведомления в телеграм.
- Полноценная ПУ в Telegram.

### :left_speech_bubble: **Уведомления и ПУ в Telegram**

- Возможность установки нескольких чатов для уведомлений.
- Уведомления о поднятии лотов.
- Уведомления о новых заказах.
- Уведомления о выдаче товара.
- Уведомления о новых сообщениях
- Возможность отвечать на сообщения прямо из Telegram.
- Возможность полностью настраивать автовыдачу / автоответчик и все остальные модули FPC.
- Возможность добавлять автовыдачу для лотов, получая лоты прямо с FunPay.

### :gear: **Дополнительные возможности**

- Использование переменных в тексте для автоответа / автовыдачи.
- Создание плагинов для кастомизации функционала без редактирования исходного кода самого Кардинала.

## :1st_place_medal: **Преимущества**

### :grinning: **Для пользователей**

- **Больше**, чем наличие самого нужного функционала.
- **Оптимизация**. _20 МБ свободного места на диске, до 50 МБ ОЗУ, доступ в интернет_ - все что нужно для работы.
- Возможность установить на **любую платформу**, которую поддерживает _Python: Windows, Linux, IOS, Android_ и т.д.
- Возможность установки плагинов дает **огромную вариативность** модификации стандартного функционала под самые разные
  нужды.
- Гибкие и при этом простые конфиги, написанные в INI-формате.
- Постоянные обновления, быстрое реагирования на баги / предложения о новом функционале.
- Полное управление через Telegram.

### :computer: **Для разработчиков**

- Выбран самый простой и при этом один из самых мощных языков для такого рода приложений - _Python_.
- Полная документация кода. Все классы / методы / функции имеют док-строки, type-хинты.
- Широкое использование ООП. Почти каждый эвент / сообщение / заказ и т.д. представляют собой экземпляр соответствующего
  класса, а не просто набор данных в JSON.
- Возможность легкого создания плагинов.
- Сконфигурированный логгер. Никаких принтов!
- Собственный Python-пакет FunPayAPI, который никак не привязан к FunPay Cardinal.
- Поддержка лично от меня :)

## :electric_plug: Плагины

- [Канал с плагинами](https://t.me/fpc_plugins)

## :arrow_down: Установка

Арендовать виртуальный сервер можно на [FirstByte](https://firstbyte.ru/?from=162840). Выбирайте Ubuntu 22-24 версии,
остальные параметры не важны.

### :large_blue_diamond: Windows

1. Скачайте и установите [Python 3.11.0](https://www.python.org/ftp/python/3.11.0/python-3.11.0-amd64.exe).
    1. При установке поставьте галочку у `Add python.exe to PATH` на первом экране установки.
2. Скачайте [FunPay Cardinal](https://github.com/sidor0912/FunPayCardinal/archive/refs/heads/master.zip)
3. Перенесите архив `FunPayCardinal-main.zip` в нужное Вам место и распакуйте его.
4. Перейдите в папку `FunPayCardinal-main`.
5. Запустите файл `Setup.bat`. Дождитесь окончания загрузки пакетов.
6. Закройте командную строку и запустите файл `Start.bat`.

### :hotsprings: Linux (Ubuntu)

1. Выполните команду:
   `wget https://raw.githubusercontent.com/sidor0912/FunPayCardinal/main/install-fpc.sh -O install-fpc.sh && bash install-fpc.sh`
2. Следуйте инструкциям установщика.
   Данный скрипт автоматически установит всё необходимое и запустит бота как фоновый процесс.

## :electric_plug: Установка плагинов

Не устанавливайте плагины из непроверенных источников. Через систему плагинов злоумышленники могут получить полный
доступ к Вашему устройству или аккаунту FunPay. Установка плагинов крайне проста.

1. Введите команду `/menu` в диалоге с ботом Telegram.
2. Нажмите кнопку `🧩 Плагины`.
3. Нажмите кнопку `➕ Добавить плагин`.
4. Отправьте или перешлите боту файл плагина.

## :question: Мне нужна помощь

Если у вас остались какие-либо вопросы, мы с радостью ответим на них в
нашем [Telegram чате](https://t.me/funpay_cardinal).

## :star: Star it

Если вам удобно пользоваться FunPay Cardinal, не забудьте поставить :star: звезду :star: данному проекту в правом
верхнем углу GitHub-страницы (нужно быть авторизованным в свой аккаунт) :)

## HStore → FunPay offer import (web admin)

Open **Lots → Import from HStore**, or `/imports` in the web admin.

1. Paste an English HStore product URL, for example
   `https://hstora.com/en/product/1358-twitter-2006-2018-microsoft-email-auth-token-2fa`.
2. Click **Import preview**. Review or edit the copied title and description.
   Descriptions are limited to 817 characters; longer imports are shortened in the
   preview with a notice, and the publish endpoint rejects descriptions over the limit.
3. Choose a FunPay category ID (`1259` for Twitter accounts), quantity, and markup percentage.
4. Review the calculated unit price and click **Publish to FunPay**.

The selling price is `supplier price × (1 + markup / 100)`, rounded **up** to the
nearest cent. For example, `$0.50` with `30%` markup becomes `$0.65`. FunPay fees
are not included. Both source and FunPay prices must be USD; other currencies
and categories requiring extra selection fields are rejected rather than guessed.
The English text is copied into both FunPay language fields.

Publishing uses the FunPay account configured in `configs/_main.cfg`, including
its user agent and enabled proxy. The browser login is independent of this account.
Each successful publication automatically creates an enabled HStore delivery mapping
in **Lots**, using the exact published title and supplier product ID. It creates the
local product file when missing and preserves existing inventory and custom response
text for compatible mappings. Cardinal is automatically restarted to load the mapping.
No HStore purchase occurs during publication; the already configured API Delivery
plugin purchases and delivers goods when a paid order arrives.

If the published title already maps to a different provider/product, publishing is
blocked until the conflict is resolved in Lots. If mapping or restart fails after
publication, the success page reports that delivery setup needs attention; do not
publish the offer again. Stock-file auto-disable/restore are disabled for imported
lots because goods are fetched on demand and the supplier stock monitor manages
availability. Global auto-delivery and the existing API Delivery plugin must remain
enabled with working HStore credentials.

A preview lasts 24 hours. Price and stock are checked again before publishing.
Publication records in `storage/cache/offer_imports.sqlite3` prevent repeated
publication of the same HStore product to the same category/account. Preserve this
file across restarts. If you manually delete a successfully published offer on
FunPay, import a fresh preview and check **I deleted the previous offer from FunPay**
to republish it. Matching live titles still block publication. Reusing the same
submitted preview is blocked, as are records with an uncertain outcome.
If a request times out after submission, check FunPay manually:
the record remains locked because the offer may already exist. Changing an existing
listing should be done through FunPay; this importer creates new listings only.

Install/update dependencies with `python -m pip install -r requirements.txt` and
restart the web admin after deploying. For local development:

```sh
python -m uvicorn admin.main:app --host 127.0.0.1 --port 7430
```

Tests use mocked FunPay publication requests and do not create live offers:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## Web admin sign-in

The web admin uses a `/login` page instead of a browser HTTP Basic password popup.
Keep using the username and password in `configs/admin.cfg`; no credential reset
or migration is required. Unauthenticated requests redirect to sign-in and return
to the requested page after successful login. Use **Sign out** in the navigation
to end a session.

Sessions expire after eight hours, persist across PM2 restarts, and are invalidated
when the configured username or password changes. Session tokens are stored only
as hashes in `storage/cache/admin_sessions.sqlite3`. Cookies are HttpOnly and
SameSite=Strict, and use Secure when the app receives HTTPS requests. If HTTPS is
terminated by Nginx, configure Uvicorn to trust forwarded headers from that proxy.
The login form has no external script or stylesheet dependency.

Deploy the updated files and restart `funpay-admin`; `funpay-cardinal` does not
need restarting for this change. A separately configured Nginx `auth_basic` rule
can still display its own popup; this change replaces the application's login only.

## Imported offer stock monitoring

Every new successful import now saves a durable HStore → FunPay stock mapping in
`storage/cache/offer_stock.sqlite3`, separate from delivery configuration. Preserve
this file across deployments. The **Imported Offer Stock Monitor** Cardinal plugin
checks these products about once a minute using HStore's live `/api/product-stock`
endpoint (the same inventory feed as the storefront), rather than the initial HTML. When a successful check returns zero
stock, it fetches the mapped offer, unchecks **Active**, saves, and verifies the
saved state. Failed supplier checks never count as zero. When stock returns, the monitor reactivates offers it previously paused and caps
the restored quantity at the available supplier stock. Manually disabled offers
stay disabled. Pause ownership survives restarts; old mappings without ownership
information are not automatically reactivated.

The Imports page shows the saved product, FunPay link, last known stock, and monitor
status. The last-check timestamp uses server time; checks older than five minutes
are marked overdue rather than appearing healthy. If FunPay's creation response does not include an offer ID, the monitor
resolves a single exact matching title in that account/category before saving the
permanent ID. Ambiguous matches are left unchanged. The monitor only handles the
FunPay account used by the running Cardinal process.

Older completed imports are recovered from their saved preview when available.
For an existing offer or an edited title, open **Imports → Link an existing FunPay
offer**, enter the HStore product URL and the complete FunPay edit URL containing
both `node` and `offer`, and save. The configured FunPay account must have access
to that editor. This action creates a monitoring link; it does not publish an offer
or change its delivery configuration.

Deploy the plugin and service files, then restart **both** PM2 processes:

```sh
pm2 restart funpay-admin funpay-cardinal
```

Ensure **Imported Offer Stock Monitor** is enabled in Cardinal's plugins. Monitor
failures appear in the Cardinal logs under `FPC.offer_stock`.

## Long credential delivery

HStore records are now saved intact: long refresh tokens and other fields are never
removed. Recognized five-section `----` account records containing a four-part `|` email
section are delivered as labeled username, password, email, email password, 2FA,
auth token, and email client ID. The recognized email refresh-token field is omitted
from buyer messages at the operator’s request; the original supplier/stock record
retains it. With no configured format, a delimiter-bounded field beginning with `M.C`
and at least 100 characters long is omitted from buyer delivery using the
operator-requested heuristic. Other long fields are retained. Delivery first sends
cleaned credentials intact. Only an explicit FunPay long-word rejection triggers
formatting of the rejected message: delimiter-separated short values stay whole,
and individual values over 80 characters use numbered bracketed parts of up to
64 characters. Buyers join only those parts without adding spaces or newlines.
An accepted message is never resent during this fallback; ambiguous failures such
as timeouts do not trigger an automatic resend. HTTP POST requests are also excluded
from automatic transport retries.

All delivery uses plain-text mode, so strings resembling `$photo` or `$sleep`
inside credentials are not interpreted as commands. Messages still follow Cardinal's
20-line splitting. Raw HStore credentials are no longer written to the plugin log.
Previously shortened stock records are not repaired by this change; recover the
original purchased record from HStore before retrying an affected order. Do not
purchase another account merely to retry delivery.

## Supplier title and price changes

The Cardinal monitor also checks the supplier product title and unit price. Mappings
keep their HStore product ID and FunPay offer ID, even when the title/URL slug changes.
New imports save the selected markup, and supplier price changes recalculate the
FunPay unit price using that same markup and cent rounding. Older mappings require an explicitly confirmed markup in the Imports table.
Previously inferred percentages are not displayed or used for automatic pricing.
Until a markup is confirmed, existing FunPay prices remain unchanged while title
and stock monitoring continue. Use the per-offer **Save %** form to choose the markup;
the next successful monitor cycle applies it, even if the supplier price has not changed.

When the supplier title changes, the monitor prepares the matching API/auto-delivery
configuration under the new title and reloads Cardinal's delivery configuration
before updating and verifying the FunPay title. Supplier titles longer than 100
characters are shortened to fit FunPay, while the complete supplier title remains
saved as the change-tracking baseline. The old title is retained as an alias
for pending orders, sharing the original stock file and delivery response. Removing
one alias from Lots preserves the stock file while another mapping still uses it.
Conflicting titles/mappings and failed updates are reported rather than overwriting
unrelated offers. Descriptions and manual activation choices are preserved.

The Imports table shows the last supplier price and an editable, explicitly confirmed markup. Updates
run in the existing monitor cycle; deploy and restart both `funpay-admin` and
`funpay-cardinal` to enable them. Product redirects are only followed within
`https://hstora.com/en/product/` and must retain the same product ID.


## Optional per-offer delivery format

Set **Delivery format (optional)** in Lots when adding/editing a mapping, or use
**Save format** on an existing row. Leave it blank to use automatic recognition and
the long `M.C` token heuristic. Saving the format restarts Cardinal to load it;
automatic imports preserve an existing format, and supplier-title aliases copy it.

Example:

```text
username----password----email|email_password|refresh_token|client_id----2fa----auth_token
```

Use the seller's exact separators. Supported field names are `username`, `password`,
`email`, `email_password`, `refresh_token`, `client_id`, `2fa`, `auth_token`, and `ct0`.
Use each field name at most once. Configured `refresh_token` fields are omitted
regardless of prefix or length; other fields, including a configured password
starting with `M.C`, are preserved. Clearing the format restores automatic behavior.
A record that doesn't match the format falls back to the heuristic and safe
chunking rather than delivering incorrectly labeled values.

This is a formatting step only: the original supplier/stock data is unchanged.
The prefix/length fallback is a heuristic, not a guarantee that a field is a refresh
token; provide an explicit format whenever the seller's layout is known.


## Removing an imported offer

On **Imports**, click **Remove** beside the mapped offer. The confirmation page
shows its title, supplier product ID, and FunPay offer ID. Confirming removes the
FunPay listing, stops stock/title/price monitoring, and removes this offer's delivery
aliases. Delivery mappings shared by another tracked offer are retained, and purchased
inventory files are kept for recovery. Handle pending orders before removing their
mapping.

Removal verifies the configured FunPay account/category and confirms the remote offer
is absent before deleting local delivery settings. If the remote offer was already
deleted, the local mapping can still be cleaned up. Failure leaves monitoring paused
and provides a retry form; do not re-import until cleanup succeeds. The removal record
prevents the migration task from restoring the offer, while clearing the old publication
guard allows a fresh import later. Cardinal is restarted automatically after cleanup.
No existing offer is removed merely by deploying this feature.

## Djekxa and Darkstore account offers

`/imports` accepts HStore, `https://djekxa.ru/product/ID`, and Darkstore
`https://dark.shopping/product/SLUG` or `/products/view/SLUG` links. Configure each
supplier's API key in Providers first. SMMCost retains its separate SMM workflow.
Imports create enabled `/lots` delivery mappings, retain the optional delivery
format, enforce the 817-character description limit, and monitor stock, titles,
and supplier prices. Identical product IDs from different suppliers remain separate.
Existing HStore database rows migrate automatically without losing paused states.

Djekxa and Darkstore API prices are in RUB. Enter **RUB for 1 USD** before publishing;
Cardinal saves that rate with the selected markup for later price updates. Change
the saved rate and markup in the Imports table. No exchange rate is guessed.
Manual-delivery products and products requiring a bulk minimum above one unit
are rejected; the importer does not create bundles or enforce a FunPay minimum quantity. Supplier stock/API failures are reported as
failed checks and never treated as zero stock.

Paid account orders run in background workers so slow suppliers do not block chat
or other order events. Orders may poll for up to ten minutes per attempt, with
pending purchases resumed every minute and after restart using the saved supplier
order identity. A lost purchase response, partial delivery, invalid download,
or uncertain chat delivery requires review; these cases never trigger a second
purchase automatically. Supplier orders are tracked in
`storage/cache/supplier_orders.sqlite3`. Full supplier records still pass through
the shared refresh-token cleanup and unsplit-first delivery formatting.

API references: [Djekxa](https://djekxa.ru/docs) and
[Dark Shopping](https://dark.shopping/developer). Live purchasing requires configured
API access and funded supplier balances.

## Long-running SMMCost services

Boosting orders have no ten-minute completion deadline. Accepted SMMCost order IDs
are saved in `storage/cache/smm_orders.sqlite3`; Cardinal checks every two minutes
and resumes monitoring after restart, even when delivery takes hours or days.
Pending/in-progress states and transient API errors do not send failure messages.
Completion, partial delivery and explicit cancellation/failure receive distinct
buyer notifications. “Boosting started” is sent only after the provider returns an
order ID. A lost/invalid creation response sends a neutral verification message
and requires operator review; the bot never blindly places the order again.
Historical orders created before this persistence change are not automatically
recovered because their provider order IDs were only held in memory.

## English and Russian offer text

Import preview detects Cyrillic separately in the title and description. Russian
source text stays in the Russian fields while English translations become the
default listing; English source stays in the English fields and gains Russian
translations. Both versions are editable before publishing. Titles are limited to
100 characters and descriptions to 817 characters in each language. Automatic
supplier-title updates also preserve separate languages and delivery aliases under
both names, sharing the same inventory and provider product ID.

Translation uses MyMemory's documented public lookup API, sending only public
listing text, never purchased credentials or API keys. Results are cached locally
in `storage/cache/offer_translations.sqlite3`. Anonymous usage has a 5,000-character
daily limit; on an outage or quota exhaustion, preview keeps the source text and
lets you fill both languages manually. No offer is published automatically by
requesting a translation preview.

### Sales analytics

Open `/analytics/` in the authenticated admin and click **Refresh from FunPay**.
The background refresh reads historical sales without purchasing products or
sending buyer messages. The page shows completed sales, units, gross revenue,
pending orders and refunds for 7/30/90/365 days or all time, with offer rankings
by completed sales or gross revenue. Currencies are reported separately.
Revenue is the order amount reported by FunPay, not profit or net payout.
Rankings group historical descriptions within each category; renamed offers can
appear separately. Refresh again to pick up new orders and changed statuses.
Snapshots persist in `storage/cache/sales_analytics.sqlite3`; an incomplete or
failed refresh preserves the previous snapshot. Refresh runs inside the admin
process, so restart it only after refreshing finishes, or refresh again afterward.

### Discord confirmation reminders

The optional **Discord Confirmation Reminders** Cardinal plugin records successful
new deliveries and waits 24 hours after delivery before requesting confirmation.
Configure the user account token and support channel ID in admin **Settings**,
then enable automatic requests. Keep the plugin enabled in Cardinal. Settings
are read live; the initial plugin installation requires restarting Cardinal,
and the settings form requires restarting the admin process after deployment.
The token is stored in ignored `configs/discord_confirmation.cfg` with mode 0600
and is never returned in the settings page. Do not commit or paste user tokens.
Discord prohibits self-bots and may terminate the account.

Only a fresh FunPay order lookup reporting `PAID` permits posting
`#ORDER_ID confirm please.` Confirmed/refunded orders are skipped. The message
contains no credentials or mentions. Successful message IDs and delivery times
persist in `storage/cache/discord_confirmation.sqlite3`. Rate limits defer the
next attempt; network failures, rejected requests, or uncertain receipts require
manual review without automatic retries. A crash during a send leaves `sending`,
which also requires review, preventing blind duplicate sends after restart.
Settings displays waiting/sent/review counts. Review the channel and Cardinal
logs before resolving any uncertain send; no automatic retry button is provided.

SMM orders start the timer only after the provider reports service completion;
placement acknowledgments and partial/failed services do not qualify. Historical
and manual deliveries are not inferred or backfilled. No requests are sent until
the feature is enabled with a token and channel configured.
