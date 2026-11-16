# Changelog

> For the complete documentation index, see [llms.txt](/llms.txt). Markdown versions of documentation pages are available by appending `.md` to the page URL.

> The latest features and updates to the OpenAI API.

Upcoming deprecations are listed on the [deprecations page](/api/docs/deprecations).

## November, 2026

### Nov 5

Feature · Model: gpt-6-cyber

Use [GPT-6 Cyber](https://developers.openai.com/api/docs/models/gpt-6-cyber) to investigate vulnerabilities, reproduce them in an authorized test environment, and verify fixes. See [Daybreak setup](https://developers.openai.com/api/docs/guides/daybreak#set-up-gpt-6-cyber) before sending your first request.

### Nov 4

Feature · Model: gpt-image-2.6-sunburst · Model: gpt-image-2.6-flare · API: v1/images/generations · API: v1/images/edits

Released [GPT-Image-2.6 Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst) and [GPT-Image-2.6 Flare](https://developers.openai.com/api/docs/models/gpt-image-2.6-flare) for image generation and editing, with adjustable complexity, SVG generation, and animated GIF generation. Added markup-guided editing for supported GPT-Image-2.5 and GPT-Image-2.6 models.

See the [image generation guide](https://developers.openai.com/api/docs/guides/image-generation#gpt-image-26).

### Nov 2

Feature · Model: gpt-live-1 · API: v1/live/sessions

Outbound SIP calling is now available to all organizations through the Live API. Connect GPT-Live to a phone number through your SIP provider. See the [SIP guide](https://developers.openai.com/api/docs/guides/voice-sip?api=live#place-an-outbound-call).

## October, 2026

### Oct 30

Update · API: v1/agents · API: v1/agents/sessions

You can now [update the tools in an existing session](https://developers.openai.com/api/docs/guides/agents-api/configuration#update-settings-for-an-existing-session) in the Agents API public beta. Updates replace the full tool list. Function tools and MCP servers change on the next model call; built-in tools change by the next turn.

Context compaction now runs in the background by default. Set `compaction.async` to `false` for blocking compaction. Background compaction may increase token usage.

Added opt-in experimental time awareness. Set `experimental.time_awareness` to `true` to give the model periodic current-time reminders. It is disabled by default. See the [agent configuration reference](https://developers.openai.com/api/reference/resources/beta/subresources/agents/methods/create) for compaction and experimental settings.

### Oct 30

Feature · API: v1/agents/sessions

Added automatic review for eligible Agents API projects. Set the session's `review` field to `"auto_review"` to have a model approve or deny supported tool and permission requests based on conversation intent and risk. See the [automatic review guide](https://developers.openai.com/api/docs/guides/agents-api/sessions/approvals) for supported requests, limitations, and additional token billing.

### Oct 30

Feature · Model: gpt-6-rosalind

Released [GPT-6 Rosalind](https://developers.openai.com/api/docs/pricing#life-sciences-models), bringing specialized biological reasoning with scientific tool use to complex life sciences research. Access is available to organizations approved for Rosalind Research through the [trusted-access program](https://help.openai.com/en/articles/20001193-gpt-rosalind-for-life-sciences-research) for internal research by authorized users.

### Oct 28

Feature

Eligible ChatGPT Pro and Business subscribers can now claim up to $500 per month in free API credits as a subscription benefit. Activate the benefit from ChatGPT or API Platform by linking an API organization you own. See [API credits for ChatGPT subscribers](https://developers.openai.com/api/docs/guides/chatgpt-api-credits) for credit amounts, eligibility, and how to claim.

### Oct 26

Feature · Model: gpt-6.1-terra · API: v1/responses · API: v1/chat/completions

Released [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra), a fast, efficient model for visual reasoning, precise computer use, and clear, natural writing.

See the [GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra#gpt-61-terra) for prompting and migration guidance.

### Oct 23

Feature · API: v1/agents/automations

Added [scheduled automations](https://developers.openai.com/api/docs/guides/agents-api/sessions/automations) to the Agents API public beta. Schedule one-time or recurring tasks in new or existing sessions. Enable the `automations` tool to let an agent schedule its own follow-ups in the current session.

### Oct 22

Update · Model: gpt-6-luna · API: v1/decisions

The [Decisions API](https://developers.openai.com/api/docs/guides/decisions) is now generally available with `gpt-6-luna`. Cache shared input across requests for prompt-cache discounts, include images in individual question instructions, and pass structured data directly with `input_json`. Each request now supports up to 400 questions, increased from 200 in public beta. Review the [changes since public beta](https://developers.openai.com/api/docs/guides/decisions#whats-new).

### Oct 21

Feature

You can now choose which service tiers projects can use in Platform settings. Organization settings can restrict service tiers for all projects. See [service tier errors](https://developers.openai.com/api/docs/guides/error-codes).

### Oct 20

Feature · API: v1/responses

You can now choose 12-hour prompt caching in the API. 12-hour prompt caching keeps shared instructions, tools, and context cached longer to benefit from lower cached-input pricing, even when requests are hours apart. 12-hour cache writes cost 2.5x the input token price. Read more [here](https://developers.openai.com/api/docs/guides/prompt-caching).

### Oct 16

Feature · Model: gpt-live-1 · API: v1/audio/voices · API: v1/live/sessions

Released [prompt-to-voice](https://developers.openai.com/api/docs/guides/custom-voices#create-a-voice-from-a-description) for all API customers. Create a custom voice from a text description through the API or voice Playground, then reuse it in GPT-Live sessions. Creating a prompt-generated voice is free; speech generation uses regular GPT-Live session billing.

Added nine [natural voices for GPT-Live](https://developers.openai.com/api/docs/guides/live-conversations#voice-options): Nuri, Juni, and Flitz (German); Brise and Sillage (French); Nira (Hindi); Harema and Shida (Japanese); and Noeul (Korean).

### Oct 16

Feature · API: v1/agents

[OpenAI-hosted sandboxes](https://developers.openai.com/api/docs/guides/agents-api/environments/openai-hosted) in the Agents API now include the Artifact SDK and skills for creating and editing spreadsheets, presentations, and Word documents.

Added [Ultrafast mode](https://developers.openai.com/api/docs/guides/ultrafast-mode) to the Agents API. Set `agent.service_tier` to `ultrafast` with a supported model to reduce the time between generated tokens.

### Oct 15

Feature · Model: gpt-live-1 · Model: gpt-6-luna · API: v1/live/sessions

Added native [Decisions integration in GPT-Live](https://developers.openai.com/api/docs/guides/decisions-voice). Your app can act while your user is speaking or react to what Live says. Let Live call Decisions directly through native delegation, or trigger decisions as input or output transcript deltas arrive.

### Oct 9

Feature · API: v1/agents

You can now [pre-warm an OpenAI-hosted sandbox](https://developers.openai.com/api/docs/guides/agents-api/environments/prewarmed) before attaching it to an Agents API session. Prewarming improves latency by creating and configuring the sandbox before a user finishes sending their message. This means the user doesn't have to wait for all this work to complete.

OpenAI-hosted sandbox compute is now billed during active turns. You no longer pay for idle time between turns.

