# Japanese transcript translation evaluation

This is a historical evaluation of DeepL and local translation options. The app now uses GPT-6 Luna; that change has not been evaluated in this document.

**Date:** 2026-10-13  
**Episode excerpt:** *Ep.6 日本の会社にはクビがない！？ | Japanese Companies Don't Fire People?!*  
**Scope:** Opening passages from approximately 0:19–0:55, where クビ means being fired.

## Result

DeepL's web translator produced the most readable result in this small comparison. It used the later definition to understand クビ as “getting fired,” translated 言われる as “when you're told,” and preserved the example that the company fires someone and the speaker gets fired. It still made interpretive choices and should not be treated as verified ground truth.

The API was not tested on this excerpt. The manual test pasted all six Japanese passages together into DeepL's web translator, first as saved and then with punctuation inserted manually without changing the words. Adding punctuation changed the output from an awkward “Because it's called dismissal…” to “That's why people aren't fired very often at Japanese companies.” In the Japanese, 解雇するだから、日本の会社では… has だから (“so/therefore”), so that reading may be correct; the excerpt alone does not establish that it is a mistranslation. The app integration was later smoke-tested with a short greeting, but this longer sample has not been compared through the API.

## Sample output

For the punctuated excerpt, DeepL returned:

> Today, I’d like to talk about how there’s no such thing as “getting fired” in Japan.  
> By “getting fired,” I don’t mean losing your neck—I mean losing your job at a company.  
> When you’re told, “You don’t need to come in starting tomorrow,” that’s what we call “getting fired.”  
> A company fires someone. I’m getting fired.  
> Or, to put it another way, we say “dismissal.”  
> Dismissal. That’s why people aren’t fired very often at Japanese companies.

The machine-translated text is useful as a draft, but needs review. For example, 首っていうのは… literally introduces the expression 首 and then explains the idiom; DeepL's choice to say “By ‘getting fired’” smooths that explanation for an English reader rather than preserving its teaching structure.

## Local model comparison

On the same opening minute, the local OPUS-MT translator produced literal and nonsensical output, including “Japan doesn't have a head,” “Rifchapas,” and fabricated “(Laughter)” passages.

A local Qwen3-8B 4-bit MLX experiment read all nine passages together and kept their IDs. Its translation-only run correctly interpreted “Japan doesn't fire people,” restored “I'm Sayaka, the host,” and removed the invented laughter. It still confused the speaker roles in a sentence and translated 解雇する as passive “to be fired.” Enabling its thinking mode changed Sayaka to Sakura and was slower without improving reliability. See the full, untracked experiment artifacts under `data/experiments/opening-minute/` if present.

A fresh Whisper large-v3 CPU int8 pass initially skipped the spoken opening. Enabling VAD recovered it and produced six longer passages, but did not supply Japanese punctuation. Neither larger local model was established as a replacement.

## DeepL API fit (historical)

At the time, the app used DeepL's [Translate Text API](https://developers.deepl.com/api-reference/translate/request-translation), which accepted multiple source strings and returned translations in the same order. The app sent up to 50 Japanese passages per request and placed each returned translation back on the corresponding timestamped row. It also sent the episode title and up to 32 KiB of the Japanese transcript as shared context. This API integration has been removed from the app.

DeepL required an API key from `DEEPL_AUTH_KEY` in `.env` or the process environment. Audio stayed local while transcript text was sent to DeepL, and usage was character-metered under the selected plan. See DeepL's [usage and billing details](https://support.deepl.com/hc/en-us/articles/360020685720-Usage-count-and-billing-in-DeepL-API).
