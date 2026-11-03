"""Proofread, single-use originals. Each entry has an independent English premise."""
from .editorial import TOPICS

# In topic order: technology, SaaS, AI, AI models, cars, company life.
LINES = {
    "Hindi": (
        ("लैपटॉप ने पूछा, अभी रीस्टार्ट करूँ या बाद में? मैंने बाद में चुना। उसे पाँच मिनट बहुत बाद लगे।","A laptop interprets later as exactly five minutes."),
        ("डैशबोर्ड इतना आसान है कि उसे समझाने के लिए अलग से डैशबोर्ड बनाया गया है।","A dashboard needs a second dashboard to explain it."),
        ("मैंने एआई से कहा, बस नाम सुझाओ। अब फ़ाइल का नाम छोटा है और बातचीत के चार भाग हैं।","Choosing a short filename produces a four-part chat."),
        ("मॉडल ने जवाब देने से पहले इतना सोचा कि मेरा सवाल ही पुराना हो गया।","A model thinks long enough for its question to go stale."),
        ("कार की चाबी इतनी बड़ी है कि जेब में रखते ही बाकी सामान को पार्किंग ढूँढनी पड़ती है।","A large car key forces everything else in the pocket to find parking."),
        ("मीटिंग का नाम रखा था ‘जल्दी निपटा लें’। कैलेंडर में अब हर मंगलवार दिख रही है।","A meeting named Let's finish quickly became a weekly event."),
    ),
    "Japanese": (
        ("充電ケーブルに「速い」と書いてあった。壊れるのは確かに速かった。","A fast charging cable excels at breaking quickly."),
        ("無料プランで一番見やすいの、有料プランへのボタンだった。","The best-designed feature of the free plan is the upgrade button."),
        ("AIに夕飯を任せたら、まず冷蔵庫の中身を表にしろと言われた。もうパンでいい。","An assistant demands a fridge inventory and dinner turns into bread."),
        ("文の続きを予測するモデルに日記を見せた。明日も残業らしい。","Autocomplete reads a diary and predicts more overtime."),
        ("助手席は空いてる。説明書だけはシートベルトが要りそうな厚さだ。","A car manual is thick enough to need its own seat belt."),
        ("議事録に「次回決める」と書く係だけは、すぐ決まった。","Only the person assigned to write that the decision was postponed was chosen quickly."),
    ),
    "Simplified Chinese": (
        ("把耳机放回盒子，手机终于连上了。看来它俩只想道个别。","Earbuds connect only when put away because they want to say goodbye."),
        ("试用版快到期了。软件里我用得最熟的功能，是关掉到期提醒。","The most practiced trial feature is dismissing the expiry reminder."),
        ("我请助手把菜谱缩短一点。它删掉了盐，保留了前言。","An assistant shortens a recipe by removing the salt but keeping the preface."),
        ("模型最有把握的答案，是它刚刚改掉的上一个答案。","The model is most confident about the answer it just replaced."),
        ("车机问我要不要保存座椅位置。我只想把找这个按钮的路线也存一下。","A driver wants to save the route to the save-seat-position button."),
        ("投影仪没连上，大家只好直接说重点。散会时，没人再提修投影仪的事。","The broken projector makes the meeting so brief nobody wants it fixed."),
    ),
    "Spanish": (
        ("El móvil me pidió espacio. Borré la app que me avisaba de que faltaba espacio. Ahora estamos los dos más tranquilos.","Deleting the storage warning app gives both phone and owner peace."),
        ("Para darme de baja tuve que elegir un motivo. Faltaba el más sincero: conocer por fin la última pantalla.","A customer cancels out of curiosity about the final screen."),
        ("Le pedí a la IA un título discreto. Me devolvió cinco, todos con mayúsculas y ganas de presentar un concurso.","An assistant gives quiet titles the energy of a game show host."),
        ("El modelo recuerda el tono que pedí hace veinte mensajes. El archivo que acabo de pegar ya es arqueología.","A model remembers an old tone request but loses a newly pasted file."),
        ("La guantera tiene de todo menos guantes. Si la llamaran cajón de recibos, por fin acertaría el manual.","Renaming the glove box after receipts would make the manual accurate."),
        ("La tarea decía «cambio pequeño». Solo hubo que cambiar el botón, el calendario y la definición de pequeño.","A small change requires redefining what small means."),
    ),
    "English": (
        ("My headphones announced “battery low” six times. There goes the battery.","Headphones spend their last charge on low-battery announcements."),
        ("Our pricing page has a slider for team size. At the far right, the price turns into a man named Greg.","The final position on a pricing slider turns the price into a sales rep."),
        ("I asked an AI to alphabetize my folders. It made a folder called Alphabetized and put everything in it.","A shortcut to alphabetizing is putting everything in a folder with that name."),
        ("The model gave itself full marks for brevity, then attached the scoring rubric.","A model defeats its own brevity score by attaching its rubric."),
        ("The car says my key is missing. A bold review from the object I just unlocked.","A car denies the presence of the key that unlocked it."),
        ("The office label maker has a label that says LABEL MAKER. Somewhere, a project actually ended.","A label maker finally labels itself and completes an office project."),
    ),
}
RESERVE = {language:dict(zip(TOPICS,lines,strict=True)) for language,lines in LINES.items()}
