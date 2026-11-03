"""Concrete writing briefs. The model writes original copy for each slot."""
TOPICS = {
    "technology": (9, (
        "the Wi-Fi printer that only works when nobody watches", "account recovery on the device you lost",
        "Bluetooth pairing that remembers the wrong speaker", "a USB-C drawer full of identical-looking cables",
        "a progress bar that reaches the end before the task does", "a smart lamp that needs an account",
        "an update reminder interrupting the update", "trying to mute the right tab", "autocorrect fixing a password",
        "an undo button hidden inside settings", "a tiny keyboard shortcut with an enormous manual",
        "a backup named final-final", "a help page with its own help widget", "a silent keyboard with a loud notification")),
    "SaaS": (8, (
        "exporting the dashboard to the spreadsheet it replaced", "a cancellation button with an onboarding flow",
        "a pricing table whose verbs get better as you scroll right", "a seat license for an empty chair",
        "three onboarding tours fighting for the same corner", "a free trial sending calendar invites",
        "a billing dashboard with its own loading screen", "the API key hidden behind a friendly wizard",
        "a status page checking another status page", "an integration that exports one comma",
        "a feature called Advanced Settings", "a settings toggle with a sales demo", "a form asking for a company size of one")),
    "artificial intelligence": (9, (
        "asking an assistant for a shorter answer", "an AI politely agreeing with a typo",
        "the prompt that becomes longer than the task", "a chatbot writing a summary of a summary",
        "asking ChatGPT to choose a filename", "an assistant trying to describe a screenshot of itself",
        "Claude explaining a one-line change", "an AI inventing a keyboard shortcut in an imaginary app",
        "a voice assistant waiting for the microwave beep", "the human quietly deleting the AI's introduction",
        "a coding agent asking permission to ask a question", "an AI's idea of a quick clarification",
        "an OpenCode session confronting a TODO comment")),
    "AI models": (8, (
        "a context window full of old lunch plans", "a benchmark that forgot the user's actual task",
        "temperature settings at a model family dinner", "a model card for an indecisive autocomplete",
        "an evaluation that rewards very confident punctuation", "a tokenizer splitting a perfectly good snack",
        "a tiny model trying to pack a long answer", "a context limit arriving at the punchline",
        "a model arguing with its own first draft", "latency measured by how much tea fits in the pause",
        "the difference between a demo prompt and a real prompt", "a retrieval system finding every document except the note")),
    "cars": (8, (
        "a touchscreen hiding the windshield wipers", "a dashboard light shaped like a tiny mystery",
        "a cupholder designed around an imaginary cup", "a car menu asking how sporty the steering wheel feels",
        "a parking sensor having strong opinions about a leaf", "a heated seat button buried in a submenu",
        "a glove box that has never met a glove", "a mechanic listening to an imitation of a noise",
        "a charging cable packed under the luggage", "a car key larger than the pocket",
        "the one trim piece that rattles only on the way home", "a software update for a physical knob",
        "an owner's manual explaining an unexplained icon")),
    "company life": (8, (
        "the meeting to shorten the next meeting", "a support ticket surviving three reorganizations",
        "a launch checklist that needs its own launch", "a product demo rehearsed around one button",
        "the handoff between design, engineering and the tooltip", "the feature flag nobody dares to remove",
        "a fictional CFO meeting a cloud storage invoice", "a tiny bug promoted to roadmap item",
        "the empty slide everyone asks to keep", "a postmortem looking for a kinder verb",
        "a meeting room named Focus", "a release note translated out of corporate language",
        "an internal tool gaining its first external dependency")),
}
LANGUAGES = {"English": 22, "Hindi": 8, "Simplified Chinese": 7, "Japanese": 7, "Spanish": 6}
FORMS = ("a dry one-liner", "a two-line exchange", "a miniature scene with a punchline",
         "a playful roast of a design choice", "an observation from an AI columnist",
         "a fictional changelog entry", "a question with a funny, concrete premise")
