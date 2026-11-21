# Privacy policy: SMSTunnel plugin for Dify

This plugin connects Dify to SMSTunnel so that your own Android phone can send texts. It is published by NARBOWEB SRL, the company behind SMSTunnel.

## What the plugin sends, and where

The plugin talks to one service only: `https://smstunnel.io`, over HTTPS.

| When | What is sent to smstunnel.io |
| --- | --- |
| Every call | Your SMSTunnel API key, in a request header |
| **Send a text** | The recipient's phone number, the words of the text and, if you chose one, the phone's ID. Inside a chat turn, also a one-way hash used to stop the same text going out twice |
| **Get text status** | The message ID |
| **List phones** | Nothing beyond the API key |

Phone numbers and the words of texts are personal data. Send only texts the recipient expects, and make sure you are allowed to contact them.

## What the plugin stores

The plugin does not store anything. It has no database, no analytics and no tracking, and it writes no logs containing your key, phone numbers or texts.

Dify stores your API key in its own credential storage. The results of each tool (such as the number, status and message ID) go back to your Dify app, and Dify may keep them in its run logs under your Dify settings. If an agent uses these tools, your chosen model provider sees the same information, under that provider's terms.

## What SMSTunnel stores

SMSTunnel keeps the texts sent from your account (number, words, status and time) so your phone can send them and you can see them in your dashboard. They stay until you delete them or your account; there is no automatic expiry. Technical server logs are kept for up to 30 days. Your phone then sends the text through your mobile carrier.

The full policy, including your rights and how to delete your data, is at https://smstunnel.io/privacy/

## Contact

- Privacy questions: privacy@smstunnel.io
- Support: support@smstunnel.io
