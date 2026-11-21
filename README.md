# SMSTunnel for Dify

Send texts from your own Android phone and your own number, from Dify agents, chatflows and workflows. The SMSTunnel app on your phone sends each text through its SIM, so the recipient sees your number and can reply to you.

It fits one-to-one texts: an order update, an appointment reminder, an alert to yourself. One phone sends about 28 SMS parts every 65 seconds, which is Android's limit.

Source repository: https://github.com/narcisbodea/smstunnel-dify

## What you need

- An SMSTunnel account (the free plan includes 25 texts a day): https://smstunnel.io
- An Android phone with the SMSTunnel app, linked to your account
- An SMSTunnel API key
- Dify 1.9.0 or newer, with outbound HTTPS access to `smstunnel.io`

## Setup

1. Install **SMSTunnel** from the Dify Marketplace.
2. In the [SMSTunnel dashboard](https://dashboard.smstunnel.io/apps), open **Apps**, choose **Add an app**, and copy the API key. It is shown only once.
3. In Dify, open **Tools**, select **SMSTunnel**, choose **Authorize**, and paste the key. Dify checks it with a read-only call, so nothing is sent.
4. Run **List phones** once to see that your phone is linked and online.

## Tools

| Tool | What it does | Sends anything? |
| --- | --- | --- |
| **Send a text** | Sends one text to one number (`to` with `+` and the country code, `message` up to 1,600 characters). Optional **Phone ID** to choose which of your phones sends it. | Yes |
| **Get text status** | Shows whether a text went out, by its message ID: `pending`, `sent`, `failed` or `unconfirmed`. | No |
| **List phones** | Shows your linked phones and which are online. | No |

`sent` is the last good status: your phone handed the text to its SIM and Android confirmed it went out. SMSTunnel can't confirm that a text arrived. The `delivered_at` output is reserved and empty today.

**Send a text** returns these variables for your workflow:

- `status`: `accepted` (handed to your phone), `queued` (no phone online; it goes out when one reconnects, within 3 days) or `unknown` (see below)
- `message_id`: pass it to **Get text status**
- `to`, `queued`, `branded` (the free plan added its short line) and `already_sent`

## Usage

**In an agent or chatflow.** Add the tools to your agent and tell it who may be texted. For example:

> Text +14155550123: your table is ready.

The tool tells the model to show the number and the exact words and to wait for your OK before sending.

**In a workflow.** Add a **Send a text** node, map `to` and `message` to your variables, and branch on `status`. Then use **Get text status** with `message_id` when you need to know the text went out.

## Rules this plugin keeps

- **Only texts people expect.** Send only texts the user asked for and the recipient expects.
- **One recipient per call.** A list of numbers is refused. To text several people, call the tool once per person.
- **International numbers only.** A number without `+` and its country code is refused, so a text can't reach a wrong number in another country.
- **No automatic resend.** The plugin makes one request per send and never retries. If the answer is lost, for example after a timeout, the result is `status: unknown`, because the text may have gone out. Check **Messages** in the SMSTunnel dashboard before sending it again.
- **Safe with Retry on Failure.** A lost answer comes back as `status: unknown`, not as an error, so Dify's node retry can't send a text twice. You don't need to turn retries on for this node.
- **The same text once per turn.** In agents and chatflows, an identical call within one conversation turn gets the first answer back and nothing is sent twice.

## Connection requirements

The plugin connects only to `https://smstunnel.io` over HTTPS and authenticates with the `X-API-Key` header. The host is fixed: no setting or tool parameter can change it, and redirects are not followed.

## Troubleshooting

- **"did not accept this API key"**: the key is wrong or deactivated. Create a new one under **Apps** in the dashboard.
- **"does not have the sms:read permission"**: the key can send but not read status. Allow it under **Apps**, or use another key.
- **`queued`**: no phone is online. Open the SMSTunnel app on your phone and check that it has signal.
- **"allowance is used up"**: your plan's daily or monthly limit is reached. See https://smstunnel.io/pricing/

## Privacy

Recipient numbers and the words of your texts go to SMSTunnel so your phone can send them. Details are in [PRIVACY.md](PRIVACY.md) and at https://smstunnel.io/privacy/

## Support

Email support@smstunnel.io or open an issue at https://github.com/narcisbodea/smstunnel-dify/issues

## Development

Python 3.12:

```bash
pip install -r requirements.txt pytest pyyaml
pytest tests
dify plugin package . -o dist/smstunnel-1.0.0.difypkg
```

The tests use a mock HTTP transport and never send a text.

## License

MIT
