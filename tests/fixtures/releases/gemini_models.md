> ## AI Agent Context
> Get the complete documentation index at: https://aistudio.google.com/llms.txt
> Use this file to discover all available pages.
> For the full documentation in a single file, fetch: https://aistudio.google.com/llms-full.txt

# Models

## Gemini 4  {% id="gemini-4" %}

{% card-grid columns="3" %}
  {% card description="Our strongest reasoning model, built for complex software engineering, multimodal knowledge work, scientific reasoning, and cybersecurity defense." title="Gemini 4 Carbon" variant="model-card" %}
  {% section class="model-specs" %}
  *   *Model code* **gemini-4-carbon**
  *   *Context* **1M tokens**
  *   *Max output* **64K / 1M tokens**
  *   *Modalities* **Text, Image, Audio, Video, PDF**

  {% /section %}

  {% section class="card-actions" %}
  {% button href="https://aistudio.google.com/prompts/new_chat?model=gemini-4-carbon" variant="primary" %}Try in AI Studio{% /button %} {% button href="/docs/models/gemini-4-carbon" icon="arrow_forward" tooltip="View models" variant="icon-primary" /%}

  {% /section %}

  {% /card %}

  {% card description="Our efficient Gemini 4 frontier reasoning model, delivering faster responses and lower token spend per turn compared to Gemini 4 Carbon across complex coding, agentic, math, and science tasks." title="Gemini 4 Argon" variant="model-card" %}
  {% section class="model-specs" %}
  *   *Model code* **gemini-4-argon**
  *   *Context* **1M tokens**
  *   *Max output* **64K / 1M tokens**
  *   *Modalities* **Text, Image, Audio, Video, PDF**

  {% /section %}

  {% section class="card-actions" %}
  {% button href="https://aistudio.google.com/prompts/new_chat?model=gemini-4-argon" variant="primary" %}Try in AI Studio{% /button %} {% button href="/docs/models/gemini-4-argon" icon="arrow_forward" tooltip="View models" variant="icon-primary" /%}

  {% /section %}

  {% /card %}

  {% card description="Brings cutting-edge intelligence to everyone, with speed and efficiency for everyday coding and agentic workflows, featuring a shared 1M context window and long decodes up to 1M tokens." title="Gemini 4 Hydrogen" variant="model-card" %}
  {% section class="model-specs" %}
  *   *Model code* **gemini-4-hydrogen**
  *   *Context* **1M tokens**
  *   *Max output* **64K / 1M tokens**
  *   *Modalities* **Text, Image, Audio, Video, PDF**

  {% /section %}

  {% section class="card-actions" %}
  {% button href="https://aistudio.google.com/prompts/new_chat?model=gemini-4-hydrogen" variant="primary" %}Try in AI Studio{% /button %} {% button href="/docs/models/gemini-4-hydrogen" icon="arrow_forward" tooltip="View models" variant="icon-primary" /%}

  {% /section %}

  {% /card %}

{% /card-grid %}

## All Gemini 4 models  {% id="all-gemini-4-models" %}

{% table variant="model" %}
  * Model
  * Endpoint

  ---

  * [Gemini 4 Carbon](/docs/models/gemini-4-carbon)
  * `gemini-4-carbon`

  ---

  * [Gemini 4 Argon](/docs/models/gemini-4-argon)
  * `gemini-4-argon`

  ---

  * [Gemini 4 Hydrogen](/docs/models/gemini-4-hydrogen)
  * `gemini-4-hydrogen`
{% /table %}

## Gemini 3  {% id="gemini-3" %}

{% card-grid %}
  {% card badge="Stable" description="Our previous-generation Flash model for software engineering, autonomous agents, and complex enterprise workflows." href="/docs/models/gemini-3.8-flash" icon="spark" title="Gemini 3.8 Flash" variant="model" /%}

  {% card badge="New" description="Default Live API model for most low-latency voice agent experiences without reasoning delays." href="/docs/models/gemini-3.8-live" icon="settings_voice" title="Gemini 3.8 Live" variant="model" /%}

  {% card badge="New" description="High-reasoning Live API model for voice interactions, recommended when higher background reasoning is required." href="/docs/models/gemini-3.8-live-extended-thinking" icon="psychology" title="Gemini 3.8 Live Extended Thinking" variant="model" /%}

  {% card badge="New" description="Flagship creative text-to-speech model for studio-grade voice fidelity, expressive acting, Voice design, and Voice replication." href="/docs/models/gemini-3.8-flash-tts" icon="graphic_eq" title="Gemini 3.8 Flash TTS" variant="model" /%}

  {% card badge="New" description="Fast, cost-efficient text-to-speech model for high-volume production, real-time voice agent cascades, and Voice replication." href="/docs/models/gemini-3.8-flash-lite-tts" icon="graphic_eq" title="Gemini 3.8 Flash-Lite TTS" variant="model" /%}

  {% card badge="Stable" description="Our previous-generation Flash model, balancing speed and multimodal capabilities across general agentic and everyday tasks." href="/docs/models/gemini-3.6-flash" icon="spark" title="Gemini 3.6 Flash" variant="model" /%}

  {% card badge="Stable" description="Our fastest, most cost-effective 3.5 model for high-throughput execution." href="/docs/models/gemini-3.5-flash-lite" icon="bolt" title="Gemini 3.5 Flash-Lite" variant="model" /%}

  {% card badge="Stable" description="Frontier-class performance rivaling larger models at a fraction of the cost." href="/docs/models/gemini-3.1-flash-lite" icon="bolt" title="Gemini 3.1 Flash-Lite" variant="model" /%}

  {% card badge="New" description="High-efficiency image generation and editing model with improved visual quality, typography, and multi-turn consistency." href="/docs/models/gemini-nano-banana-2.1" icon="banana_spark" title="Nano Banana 2.1" variant="model" /%}

  {% card badge="Stable" description="Powerful, high-efficiency image generation and editing, optimized for speed and high-volume use cases." href="/docs/models/gemini-3.1-flash-image" icon="banana_spark" title="Nano Banana 2" variant="model" /%}

  {% card badge="Stable" description="Ultra-low latency and cost-effective image generation and editing, designed for high-volume interactive use cases." href="/docs/models/gemini-3.1-flash-lite-image" icon="banana_spark" title="Nano Banana 2 Lite" variant="model" /%}

  {% card badge="Stable" description="State-of-the-art image generation and editing models for highly contextual native image creation." href="/docs/models/gemini-3-pro-image" icon="banana_spark" title="Nano Banana Pro" variant="model" /%}

  {% card badge="Stable" description="Low-latency speech-to-text model with utterance-based language detection, speaker diarization, and word timestamps." href="/docs/models/gemini-3.5-transcribe" icon="speech_to_text" title="Gemini 3.5 Transcribe" variant="model" /%}

  {% card badge="New" description="Generate and edit up to 15 seconds of video with native audio, keyframe interpolation, video extension, and character consistency." href="/docs/models/gemini-omni-flash" icon="movie_filter" title="Gemini Omni Flash" variant="model" /%}

  {% card badge="Preview" description="Frontier-class performance rivaling larger models at a fraction of the cost." href="/docs/models/gemini-3-flash-preview" icon="spark" title="Gemini 3 Flash" variant="model" /%}

  {% card badge="Preview" description="Low-latency, real-time speech to speech translation model that supports 70+ languages." href="/docs/models/gemi