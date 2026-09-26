# Candlewise user guide

*Read the market. Decide for yourself.*

Candlewise helps you research Indian stocks (NSE/BSE). It gathers prices, charts and
research into one place and explains them. **It never tells you to buy, sell or hold**,
and nothing in it is investment advice. Your decisions are your own.

> Each Candlewise installation has **one user**. If someone set it up for you, it's yours
> alone. There's no shared sign-up page.

## 1. First sign-in

1. **Create your login.** Open the address you were given, for example
   `https://your-domain` or `http://localhost:5183`. On the very first visit you'll see
   **Set an access password**. Choose a username and a strong password. From then on you
   use **Sign in**.
2. **Read the notice.** A **Before you continue** notice explains that Candlewise gives
   research and education only. Tick **I understand…** and continue. You'll see this again
   whenever the notice changes.
3. **Take the tour.** A short welcome tour follows. You can skip it.

The disclaimer stays visible at the bottom of every page, on every research summary and
in every notification.

**Navigation:**

- **On a phone:** tabs at the bottom (Home, Portfolio, Research, Alerts), plus **More**
  for Simulation, Agents, History, Data sources and Settings.
- **On a computer:** the same pages are in the top bar and the account menu. Switch
  between light and dark themes from the account menu.

## 2. Connect your broker (for live prices)

Candlewise reads prices with **your own** broker API app, so it can only see what your
broker API allows. It **never places orders**.

1. Go to **More → Data sources**. On a computer, it's in the account menu.
2. Choose **Zerodha Kite Connect**, **Upstox** or **Angel One SmartAPI**, and follow the
   note on its card. You'll need to create an API app with your broker first; the
   administrator's guide is [SETUP.md §7](SETUP.md#7-broker-api-keys).
   - **Kite** and **Upstox:** enter your app keys, then **Log in**. You're sent to the
     broker and back.
   - **Angel One:** enter your API key, client code and PIN. At each login, type the
     6-digit code from your authenticator app.
3. **Log in again each day.** Broker sessions expire every day. If prices stop updating,
   log in again on **Data sources**.

Your keys are encrypted and shown again only as a masked hint.

> Broker connections have been tested with sample data only, not yet with live accounts.
> If something doesn't work, tell your administrator.

## 3. Build your watchlist and portfolio

**Add stocks:** go to **Portfolio → Add stock**, search by NSE symbol or name (for example
`INFY` or `Infosys`), and add it. If search can't find a stock, you can still add the
symbol you typed; it's marked unverified.

**Portfolio and Watchlist tabs:**

- **Portfolio** shows your accounts and positions.
- **Watchlist** shows stocks you follow without holding them.
- Use **Add account** to create an account, then **+** on it to add a position (quantity
  and average cost).
- Candlewise doesn't import holdings from your broker; you enter them yourself.

**How numbers are shown:**

- **Money** is in rupees, with lakh (L) and crore (Cr) for large amounts, e.g. ₹2.5 L.
- **Changes** have a sign and an arrow as well as a colour: ▲ +1.20% (green) is up and
  ▼ −0.80% (red) is down.
- **Times** are in IST.

**Refreshing:** **Refresh** updates prices. **Auto refresh** repeats that every few
seconds while the page is open.

## 4. Read a stock page and its research summary

Tap a stock's **symbol or name** anywhere (Home, Portfolio, Watchlist) to open its page.
The eye icon opens a **Quick view** instead, without leaving the list.

**What's on the page:**

- **Price header:** price, change, when it was fetched (IST), and a data-quality label
  such as "Delayed / unofficial". Below that: open, high, low, previous close, volume and
  5-day/20-day change.
- **Research summary: what the evidence says.**
  - **Summary:** two or three sentences on what the data shows.
  - **Bull case / Bear case:** the strongest factual points for and against. These are
    arguments, not recommendations.
  - **Key risks.**
  - **Key levels:** support and resistance computed from daily prices, with their
    distance from the last close. They describe price history; they aren't entry points
    or stop-losses.
  - **Upcoming events:** results, dividends and similar, if the research mentions any.
  - **Sources:** where each piece came from, with IST timestamps. Always check the
    sources.
  - The disclaimer is always at the bottom.
- **Price chart, Technical snapshot and News & filings.** The technical snapshot gives
  descriptive readings such as "bearish alignment" or "overbought"; these aren't signals.

**Getting a summary.** If you see **No research summary yet**, press **Deep research**.
It's only available for stocks in your portfolio or watchlist.

- The run brings together analyst reports and a bull-versus-bear debate from several AI
  agents.
- It takes a few minutes; the README says 3–5.
- It uses your administrator's AI provider, and each run has a cost.
- **Full report** opens the complete write-up, which you can also export as a PDF.

**Things to keep in mind:**

- AI can be wrong or out of date.
- Indian news and exchange filings aren't connected yet, so summaries rely on price data
  and whatever the AI analysts could find.

You can also ask the assistant about a stock: press **Ask** on the stock page, or open
**Research**. It explains and looks things up, but won't give buy/sell calls. If you ask
for one, it will explain why it can't.

## 5. Alerts

Alerts tell you when something you chose happens. They never trade.

1. **Set up a channel.** Go to **More → Settings → Notification channels** and add
   Telegram, Discord or Pushover. Use **Test** to check it works.
2. **Create a rule.** Go to **Alerts → New rule**. Pick a stock and conditions: price,
   change %, turnover or volume ratio, combined with AND/OR. Then choose:
   - **when it applies:** market hours only (09:15–15:30 IST), or all day;
   - **cooldown and daily limit:** how often it may notify you;
   - **repeat mode, expiry and channels.**
3. **Wait or test.** Rules are checked every minute. **Scan now** runs a check straight
   away. Each alert shows its recent hits.

Every notification ends with the disclaimer.

**Agents** (**More → Agents**) can also send you scheduled research. The **Daily close
report** is on by default at 15:30 IST on weekdays. The **Pre-market outlook** (09:00) and
the **Intraday monitor** can be switched on there.

Note: Candlewise doesn't yet know NSE holidays, so agents may also run on a holiday.

## 6. Simulation (paper trading)

**More → Simulation** is a practice account. It's always labelled **Simulation**: no real
orders, no real money.

- It shows simulated total assets, returns, win rate, max drawdown, open positions,
  closed trades and a return curve.
- **Reset** starts over with the default simulated capital.
- **You can't open new trades in research-only mode.** Automatic AI trades are switched
  off, and entering simulated orders yourself isn't available yet. Past simulation
  history stays visible.
- The simulation's costs don't yet reflect Indian charges (STT, stamp duty and so on).

## 7. History and settings

- **History** lists past reports from your agents, newest first, with IST times.
- **Settings** is where you manage:
  - AI services and models (usually done by your administrator);
  - notification channels, including quiet hours;
  - the public link used in notifications;
  - config export and import;
  - the version shown at the bottom.
- The **self-check** in the account menu tests your setup: AI, broker, notifications.

## Getting help

If a page shows **Couldn't load…**, press **Try again**. If it keeps failing, check your
broker login on **Data sources**, then contact whoever runs your installation.
