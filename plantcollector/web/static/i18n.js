/*
  Languages for CCI Plant Monitoring: English (the page's own text), Arabic, Kurdish (Sorani) and Turkish.
  The pages are written in English; this file translates what is on screen, including what the dashboard redraws
  every few seconds (a MutationObserver translates new text as it appears). Arabic and Kurdish switch the layout
  to right-to-left. To correct a word, change it here: the key is the English text exactly as on screen.
  The Kurdish (Sorani) texts should be checked by a native speaker on site.
*/
(() => {
  const LANGS = { en: { name: "English", dir: "ltr" }, ar: { name: "العربية", dir: "rtl" }, ckb: { name: "کوردی", dir: "rtl" }, tr: { name: "Türkçe", dir: "ltr" } };
  // [English, Arabic, Kurdish (Sorani), Turkish]
  const T = [
    // alarms and warnings
    ["and", "و", "و", "ve"], ["to acknowledge", "بحاجة إلى إقرار", "پێویستی بە پەسەندکردنە", "onaylanacak"], ["appeared", "ظهرت", "دەرکەوتن", "geldi"],
    ["Alarm", "إنذار", "ئاگاداری", "Alarm"], ["Alarms and warnings", "الإنذارات والتحذيرات", "ئاگاداری و هۆشدارییەکان", "Alarmlar ve uyarılar"],
    ["Alarm sound", "صوت الإنذار", "دەنگی ئاگاداری", "Alarm sesi"], ["Warning sound", "صوت التحذير", "دەنگی هۆشداری", "Uyarı sesi"],
    ["Sound on a new alarm or warning (repeats every minute until acknowledged)", "صوت عند إنذار أو تحذير جديد (يتكرر كل دقيقة حتى الإقرار)", "دەنگ بۆ ئاگاداری یان هۆشداری نوێ (هەر خولەکێک دووبارە دەبێتەوە تا پەسەند دەکرێت)", "Yeni alarm veya uyarıda ses (onaylanana kadar her dakika)"],
    ["Windows notification on a new alarm or warning", "إشعار ويندوز عند إنذار أو تحذير جديد", "ئاگادارکردنەوەی ویندۆز بۆ ئاگاداری یان هۆشداری نوێ", "Yeni alarm veya uyarıda Windows bildirimi"],
    ["Pulse", "نبض", "لێدان", "Nabız"], ["Rising", "تصاعدي", "بەرزبوونەوە", "Yükselen"], ["Two-tone", "نغمتان", "دوو دەنگ", "İki tonlu"],
    ["Ding-dong", "دينغ دونغ", "دینگ دۆنگ", "Ding-dong"], ["Soft chime", "رنين هادئ", "زەنگی نەرم", "Yumuşak çan"], ["Double tap", "نقرتان", "دوو لێدان", "Çift vuruş"],
    ["new on this screen", "جديد على هذه الشاشة", "نوێ لەم شاشەیە", "bu ekranda yeni"],
    ["Click a sound to hear it and keep it. When alarms and warnings arrive together only the alarm sound plays.", "انقر على صوت لسماعه واختياره. عند وصول إنذارات وتحذيرات معاً يُشغَّل صوت الإنذار فقط.", "کرتە لە دەنگێک بکە بۆ بیستن و هەڵبژاردنی. کاتێک ئاگاداری و هۆشداری پێکەوە دێن تەنها دەنگی ئاگاداری لێدەدرێت.", "Dinlemek ve seçmek için bir sese tıklayın. Alarm ve uyarı birlikte gelirse yalnızca alarm sesi çalar."],
    ["Classic", "كلاسيكي", "کلاسیک", "Klasik"], ["Now pick the last day", "اختر الآن اليوم الأخير", "ئێستا دوایین ڕۆژ هەڵبژێرە", "Şimdi son günü seçin"],
    ["Check the times (for example 06:30).", "تحقق من الأوقات (مثلاً 06:30).", "کاتەکان بپشکنە (بۆ نموونە 06:30).", "Saatleri kontrol edin (örneğin 06:30)."],
    ["Classic is the standard sound. Click a sound to hear it and keep it. When alarms and warnings arrive together only the alarm sound plays.", "الصوت الكلاسيكي هو الافتراضي. انقر على صوت لسماعه واختياره. عند وصول إنذارات وتحذيرات معاً يُشغَّل صوت الإنذار فقط.", "کلاسیک دەنگی بنەڕەتییە. کرتە لە دەنگێک بکە بۆ بیستن و هەڵبژاردنی. کاتێک ئاگاداری و هۆشداری پێکەوە دێن تەنها دەنگی ئاگاداری لێدەدرێت.", "Klasik varsayılan sestir. Dinlemek ve seçmek için bir sese tıklayın. Alarm ve uyarı birlikte gelirse yalnızca alarm sesi çalar."],
    // live reading
    ["Last 15 minutes · live", "آخر 15 دقيقة · مباشر", "دوایین 15 خولەک · ڕاستەوخۆ", "Son 15 dakika · canlı"],
    ["Devices the master reads", "الأجهزة التي يقرؤها المتحكم الرئيسي", "ئەو ئامێرانەی کۆنترۆڵەری سەرەکی دەیانخوێنێتەوە", "Ana kontrolörün okuduğu cihazlar"],
    ["COMM FAIL", "انقطاع الاتصال", "پەیوەندی پچڕاوە", "İLETİŞİM HATASI"], ["Read", "القراءة", "خوێندنەوە", "Okuma"],
    ["master slow or not answering", "المتحكم الرئيسي بطيء أو لا يستجيب", "کۆنترۆڵەری سەرەکی خاوە یان وەڵام ناداتەوە", "ana kontrolör yavaş veya yanıt vermiyor"],
    ["Reading the master…", "جارٍ قراءة المتحكم الرئيسي…", "خوێندنەوەی کۆنترۆڵەری سەرەکی…", "Ana kontrolör okunuyor…"], ["No values yet.", "لا توجد قيم بعد.", "هێشتا هیچ بەهایەک نییە.", "Henüz değer yok."],
    ["What feeds the plant, at the master's live rate (Settings > Reading the devices): solar, gensets and grid stacked, the site load as a dashed line. From the master controller, which already knows every source, so nothing extra is asked of the SAU or the incomers. The Turkey feed does not follow these values; it changes at the site read.",
     "ما يغذي المحطة بمعدل القراءة المباشرة للمتحكم الرئيسي (الإعدادات > قراءة الأجهزة): الطاقة الشمسية والمولدات والشبكة مكدسة، وحمل الموقع خط متقطع. من المتحكم الرئيسي الذي يعرف كل مصدر، فلا يُطلب شيء إضافي من SAU أو المغذيات. تغذية تركيا لا تتبع هذه القيم؛ تتغير عند قراءة الموقع.",
     "چی وێستگە دەداتەوە، بە خێرایی خوێندنەوەی ڕاستەوخۆی کۆنترۆڵەری سەرەکی (ڕێکخستنەکان > خوێندنەوەی ئامێرەکان): خۆر، مۆلیدەکان و گرید لەسەر یەک، لۆدی شوێن وەک هێڵی پچڕپچڕ. لە کۆنترۆڵەری سەرەکییەوە، کە هەموو سەرچاوەکان دەزانێت، بۆیە هیچ شتێکی زیاتر لە SAU یان هێڵە هاتووەکان داوا ناکرێت. داتای تورکیا شوێن ئەم بەهایانە ناکەوێت؛ لە خوێندنەوەی وێستگەدا دەگۆڕێت.",
     "Santrali ana kontrolörün canlı okuma sıklığında ne besliyor (Ayarlar > Cihazların okunması): güneş, jeneratörler ve şebeke üst üste, tesis yükü kesikli çizgi. Her kaynağı zaten bilen ana kontrolörden; SAU'dan veya girişlerden ek bir şey istenmez. Türkiye beslemesi bu değerleri izlemez; tesis okumasında değişir."],
    ["The master's own comm-fail signal for each device it reads over the network, live. Red = the master is not getting that device's data right now. Every fail and its end is written in Alarms & events, with the time of this program's last read of that device when it came just before.",
     "إشارة انقطاع الاتصال الخاصة بالمتحكم الرئيسي لكل جهاز يقرؤه عبر الشبكة، مباشرة. الأحمر = المتحكم لا يحصل على بيانات ذلك الجهاز الآن. كل انقطاع ونهايته يُسجَّل في الإنذارات والأحداث، مع وقت آخر قراءة لهذا البرنامج لذلك الجهاز إذا جاءت قبله مباشرة.",
     "ئاماژەی پچڕانی پەیوەندیی خودی کۆنترۆڵەری سەرەکی بۆ هەر ئامێرێک کە لە ڕێگەی تۆڕەوە دەیخوێنێتەوە، ڕاستەوخۆ. سوور = کۆنترۆڵەرەکە ئێستا داتای ئەو ئامێرە وەرناگرێت. هەموو پچڕانێک و کۆتاییەکەی لە ئاگادارکردنەوە و ڕووداوەکاندا دەنووسرێت، لەگەڵ کاتی دوایین خوێندنەوەی ئەم بەرنامەیە بۆ ئەو ئامێرە ئەگەر پێش ئەوە هاتبێت.",
     "Ana kontrolörün ağ üzerinden okuduğu her cihaz için kendi iletişim hatası sinyali, canlı. Kırmızı = ana kontrolör o cihazın verisini şu anda alamıyor. Her hata ve bitişi, hemen öncesinde geldiyse bu programın o cihazı son okuma zamanıyla birlikte Alarmlar ve olaylar'a yazılır."],
    // all the remaining screens: overview details, devices, export, settings, tooltips, events, day report
    ["ON", "يعمل", "هەیە", "AÇIK"],
    ["OFF", "متوقف", "نییە", "KAPALI"],
    ["since", "منذ", "لە", "itibaren"],
    ["gensets on line", "مولدات في الخدمة", "مۆلیدە کار دەکەن", "jeneratör devrede"],
    ["breakers:", "القواطع:", "پچڕێنەرەکان:", "kesiciler:"],
    ["Erbil Coca-Cola hybrid plant", "محطة كوكاكولا أربيل الهجينة", "وێستگەی هایبریدی کۆکاکۆلای هەولێر", "Erbil Coca-Cola hibrit santrali"],
    ["Grid", "الشبكة", "گرید", "Şebeke"],
    ["grid on", "الشبكة متاحة", "گرید هەیە", "şebeke var"],
    ["grid OFF", "الشبكة مقطوعة", "گرید نییە", "şebeke yok"],
    ["RUNNING", "يعمل", "کار دەکات", "ÇALIŞIYOR"],
    ["stopped", "متوقف", "وەستاو", "durdu"],
    ["all answering", "الكل يستجيب", "هەموو وەڵام دەدەنەوە", "hepsi yanıt veriyor"],
    ["where the energy comes from, today and this week", "مصدر الطاقة، اليوم وهذا الأسبوع", "وزە لە کوێوە دێت، ئەمڕۆ و ئەم هەفتەیە", "enerji nereden geliyor, bugün ve bu hafta"],
    ["Gensets P", "قدرة المولدات", "توانای مۆلیدەکان", "Jeneratör gücü"],
    ["24 V DC supply of the controller", "تغذية المتحكم 24 فولت مستمر", "تەزووی 24 V DC ی کۆنترۆڵەر", "kontrolörün 24 V DC beslemesi"],
    ["none faulty", "لا يوجد عطل", "هیچیان تێکنەچووە", "arızalı yok"],
    ["Available (sun)", "المتاح (الشمس)", "بەردەست (خۆر)", "Mevcut (güneş)"],
    ["Delivered", "المُسلَّم", "گەیەندراو", "Verilen"],
    ["Power", "القدرة", "توانا", "Güç"],
    ["Running", "يعمل", "کار دەکات", "Çalışıyor"],
    ["PV production", "إنتاج الطاقة الشمسية", "بەرهەمی خۆر", "Güneş üretimi"],
    ["Mains", "الشبكة", "گرید", "Şebeke"],
    ["Energy today", "طاقة اليوم", "وزەی ئەمڕۆ", "Bugünkü enerji"],
    ["Cabin temp", "حرارة الكابينة", "پلەی گەرمی کابین", "Kabin sıcaklığı"],
    ["Available", "متاح", "بەردەست", "Mevcut"],
    ["Import", "استيراد", "هاوردە", "Alınan"],
    ["kW import", "kW استيراد", "kW هاوردە", "kW alınan"],
    ["on", "يعمل", "هەیە", "açık"],
    ["off", "متوقف", "نییە", "kapalı"],
    ["good", "جيد", "باش", "iyi"],
    ["bad", "سيئ", "خراپ", "kötü"],
    ["stale", "قديم", "کۆن", "eski"],
    ["every reading of every device, as Excel or CSV", "كل قراءة لكل جهاز، بصيغة Excel أو CSV", "هەموو خوێندنەوەیەکی هەموو ئامێرێک، وەک Excel یان CSV", "her cihazın her okuması, Excel veya CSV olarak"],
    ["since midnight, every 5 min", "منذ منتصف الليل، كل 5 دقائق", "لە نیوەشەوەوە، هەر 5 خولەک", "gece yarısından beri, 5 dakikada bir"],
    ["the full day, every 5 min", "اليوم كاملاً، كل 5 دقائق", "هەموو ڕۆژەکە، هەر 5 خولەک", "tüm gün, 5 dakikada bir"],
    ["a week, every 15 min", "أسبوع، كل 15 دقيقة", "هەفتەیەک، هەر 15 خولەک", "bir hafta, 15 dakikada bir"],
    ["since the 1st, every hour", "منذ أول الشهر، كل ساعة", "لە یەکی مانگەوە، هەر کاتژمێرێک", "ayın 1'inden beri, saatte bir"],
    ["one column per device point, averaged per step", "عمود لكل نقطة جهاز، بمتوسط لكل خطوة", "ستوونێک بۆ هەر خاڵێکی ئامێر، تێکڕا بۆ هەر هەنگاوێک", "her cihaz noktası için bir sütun, adım başına ortalama"],
    ["one row every", "صف واحد كل", "ڕیزێک هەر", "her satır"],
    ["formatted workbook, plant-local and UTC time", "مصنف منسق، بتوقيت المحطة وUTC", "پەڕگەی ڕێکخراو، کاتی وێستگە و UTC", "biçimli çalışma kitabı, santral saati ve UTC"],
    ["plain text, for any tool or script", "نص عادي، لأي أداة أو برنامج", "دەقی سادە، بۆ هەر ئامراز یان سکریپتێک", "düz metin, her araç veya betik için"],
    ["Times are 24-hour, in", "الأوقات بنظام 24 ساعة، حسب", "کاتەکان 24 کاتژمێرین، بە", "Saatler 24 saatlik,"],
    [". Up to 62 days per file; a bad or missing reading is an empty cell, never a zero.", ". حتى 62 يوماً لكل ملف؛ القراءة السيئة أو المفقودة خلية فارغة، وليست صفراً أبداً.", ". تا 62 ڕۆژ بۆ هەر فایلێک؛ خوێندنەوەی خراپ یان ونبوو خانەیەکی بەتاڵە، هەرگیز سفر نییە.", ". Dosya başına en çok 62 gün; kötü veya eksik okuma boş hücredir, asla sıfır değildir."],
    ["one printable page for any day · PDF", "صفحة قابلة للطباعة لأي يوم · PDF", "پەڕەیەکی چاپکراو بۆ هەر ڕۆژێک · PDF", "her gün için yazdırılabilir sayfa · PDF"],
    ["Open ›", "فتح ›", "کردنەوە ›", "Aç ›"],
    ["hourly energy, gensets, events of a UTC day", "طاقة كل ساعة والمولدات وأحداث يوم UTC", "وزەی هەر کاتژمێرێک، مۆلیدەکان، ڕووداوەکانی ڕۆژێکی UTC", "bir UTC gününün saatlik enerjisi, jeneratörleri, olayları"],
    ["Rev. 1.2 with live values · also live at", "الإصدار 1.2 مع القيم المباشرة · ومباشرة أيضاً على", "وەشانی 1.2 لەگەڵ بەهای ڕاستەوخۆ · هەروەها ڕاستەوخۆ لە", "Rev. 1.2 canlı değerlerle · ayrıca canlı:"],
    ["how often every device is read", "كم مرة يُقرأ كل جهاز", "هەر ئامێرێک چەند جار دەخوێندرێتەوە", "her cihaz ne sıklıkla okunur"],
    ["Every 10 minutes at the most. Every reading is saved in the database the moment it is read. For a one-off read at any time use \"Read devices now\" at the top. The setting is kept in", "كل 10 دقائق على الأكثر. تُحفظ كل قراءة في قاعدة البيانات لحظة قراءتها. لقراءة فورية في أي وقت استخدم \"اقرأ الأجهزة الآن\" في الأعلى. يُحفظ الإعداد في", "هەر 10 خولەک بە لایەنی زۆرەوە. هەر خوێندنەوەیەک هەر کە خوێندرایەوە لە بنکەی داتا پاشەکەوت دەکرێت. بۆ خوێندنەوەیەکی خێرا لە هەر کاتێکدا \"ئێستا ئامێرەکان بخوێنەوە\" لە سەرەوە بەکاربهێنە. ڕێکخستنەکە لە", "En çok 10 dakikada bir. Her okuma okunduğu anda veritabanına kaydedilir. İstediğiniz an tek seferlik okuma için üstteki \"Cihazları şimdi oku\" düğmesini kullanın. Ayar şurada saklanır:"],
    ["and survives restarts.", "ويبقى بعد إعادة التشغيل.", "هەڵدەگیرێت و دوای دەستپێکردنەوە دەمێنێت.", "ve yeniden başlatmada korunur."],
    ["Modbus server for Turkey", "خادم Modbus لتركيا", "ڕاژەکاری Modbus بۆ تورکیا", "Türkiye için Modbus sunucusu"],
    ["Config", "الإعدادات", "ڕێکخستن", "Yapılandırma"],
    ["Database", "قاعدة البيانات", "بنکەی داتا", "Veritabanı"],
    ["Settings file", "ملف الإعدادات", "فایلی ڕێکخستنەکان", "Ayar dosyası"],
    ["Updates", "التحديثات", "نوێکردنەوەکان", "Güncellemeler"],
    ["Reading the devices", "قراءة الأجهزة", "خوێندنەوەی ئامێرەکان", "Cihazların okunması"], ["how often each one is asked", "كم مرة يُسأل كل جهاز", "هەر یەکێکیان چەند جار پرسیاری لێدەکرێت", "her birine ne sıklıkla sorulur"],
    ["Live view", "العرض المباشر", "پیشاندانی ڕاستەوخۆ", "Canlı görünüm"], ["The other devices", "الأجهزة الأخرى", "ئامێرەکانی تر", "Diğer cihazlar"],
    ["Master controller", "المتحكم الرئيسي", "کۆنترۆڵەری سەرەکی", "Ana kontrolör"], ["Solar: the SAU logger", "الطاقة الشمسية: مسجل SAU", "خۆر: تۆمارکەری SAU", "Güneş: SAU kaydedici"],
    ["off (read with the site read)", "متوقف (يُقرأ مع قراءة الموقع)", "کوژاوە (لەگەڵ خوێندنەوەی وێستگە دەخوێندرێتەوە)", "kapalı (tesis okumasıyla okunur)"],
    ["slowed automatically: the master lost it right after our reads", "أُبطئ تلقائياً: فقده المتحكم الرئيسي بعد قراءاتنا مباشرة", "بە شێوەی خۆکار خاو کرایەوە: کۆنترۆڵەری سەرەکی دوای خوێندنەوەکانمان لەدەستی دا", "otomatik yavaşlatıldı: ana kontrolör okumalarımızdan hemen sonra onu kaybetti"],
    ["Saved: the new reading rates apply now.", "تم الحفظ: معدلات القراءة الجديدة تُطبَّق الآن.", "پاشەکەوت کرا: خێرایی نوێی خوێندنەوە ئێستا جێبەجێ دەکرێت.", "Kaydedildi: yeni okuma sıklıkları şimdi uygulanıyor."],
    ["Last hour · live", "آخر ساعة · مباشر", "دوایین کاتژمێر · ڕاستەوخۆ", "Son saat · canlı"],
    ["demo: the rates of the real plant are set on the plant PC", "عرض تجريبي: معدلات المحطة الحقيقية تُضبط على حاسوب المحطة", "نموونە: خێرایی وێستگەی ڕاستەقینە لەسەر کۆمپیوتەری وێستگە دادەنرێت", "demo: gerçek santralin sıklıkları santral bilgisayarında ayarlanır"], ["new versions from GitHub", "إصدارات جديدة من GitHub", "وەشانی نوێ لە GitHub", "GitHub'dan yeni sürümler"],
    ["Installed version", "الإصدار المثبت", "وەشانی دامەزراو", "Kurulu sürüm"], ["Newest on GitHub", "الأحدث على GitHub", "نوێترین لە GitHub", "GitHub'daki en yeni"],
    ["Update state", "حالة التحديث", "دۆخی نوێکردنەوە", "Güncelleme durumu"], ["Last check", "آخر فحص", "دوایین پشکنین", "Son kontrol"],
    ["Last update", "آخر تحديث", "دوایین نوێکردنەوە", "Son güncelleme"], ["Automatic updates", "التحديثات التلقائية", "نوێکردنەوەی خۆکار", "Otomatik güncellemeler"],
    ["Check for updates", "التحقق من التحديثات", "پشکنینی نوێکردنەوە", "Güncellemeleri denetle"],
    ["Version", "الإصدار", "وەشان", "Sürüm"],
    ["Polls since start", "عمليات القراءة منذ البدء", "خوێندنەوەکان لە دەستپێکەوە", "Başlangıçtan beri okuma"],
    ["Rows stored", "الصفوف المخزنة", "ڕیزە پاشەکەوتکراوەکان", "Saklanan satır"],
    ["Running since", "يعمل منذ", "کار دەکات لە", "Çalışma başlangıcı"],
    ["API documentation at", "وثائق API على", "بەڵگەنامەی API لە", "API belgeleri:"],
    ["© 2026 CCI Plant Monitoring ·", "© 2026 مراقبة محطة CCI ·", "© 2026 چاودێری وێستگەی CCI ·", "© 2026 CCI Santral İzleme ·"],
    ["every device answers and no controller alarm is set", "كل الأجهزة تستجيب ولا يوجد إنذار من المتحكم", "هەموو ئامێرەکان وەڵام دەدەنەوە و هیچ ئاگادارییەکی کۆنترۆڵەر نییە", "tüm cihazlar yanıt veriyor ve kontrolör alarmı yok"],
    ["Connection to the collector", "الاتصال بالمجمّع", "پەیوەندی بە کۆکەرەوە", "Toplayıcı bağlantısı"],
    ["Collapse the menu", "طي القائمة", "داخستنی مێنیو", "Menüyü daralt"],
    ["Search sections, devices and actions", "ابحث في الأقسام والأجهزة والإجراءات", "گەڕان لە بەش و ئامێر و کردارەکان", "Bölüm, cihaz ve işlem ara"],
    ["Search sections, devices and actions…", "ابحث في الأقسام والأجهزة والإجراءات…", "گەڕان لە بەش و ئامێر و کردارەکان…", "Bölüm, cihaz ve işlem ara…"],
    ["Read every device now", "اقرأ كل الأجهزة الآن", "ئێستا هەموو ئامێرەکان بخوێنەوە", "Tüm cihazları şimdi oku"],
    ["Refresh every panel now", "حدّث كل اللوحات الآن", "ئێستا هەموو پانێڵەکان نوێ بکەرەوە", "Tüm panelleri şimdi yenile"],
    ["Light / dark", "فاتح / داكن", "ڕووناک / تاریک", "Açık / koyu"],
    ["Full screen (F)", "ملء الشاشة (F)", "پڕی شاشە (F)", "Tam ekran (F)"],
    ["Full screen (Esc to leave)", "ملء الشاشة (Esc للخروج)", "پڕی شاشە (Esc بۆ دەرچوون)", "Tam ekran (çıkmak için Esc)"],
    ["Leave full screen (Esc)", "الخروج من ملء الشاشة (Esc)", "دەرچوون لە پڕی شاشە (Esc)", "Tam ekrandan çık (Esc)"],
    ["Solar power / site load, right now.", "الطاقة الشمسية / الحمل، الآن.", "سۆلەر / لۆدی شوێن، ئێستا.", "Güneş gücü / tesis yükü, şu an."],
    ["The plant's LV busbar with every incomer, the load, the solar feeder and the gensets. Green = live (voltage present), grey = dead. A breaker is drawn closed or open as its controller reports it. The arrows move in the direction the power flows; faster = more power.", "قضيب الجهد المنخفض للمحطة مع كل مغذٍّ والحمل ومغذي الطاقة الشمسية والمولدات. الأخضر = مكهرب، الرمادي = بلا جهد. يُرسم القاطع مغلقاً أو مفتوحاً كما يبلغ متحكمه. تتحرك الأسهم باتجاه تدفق الطاقة؛ الأسرع = طاقة أكثر.", "باسباری ڤۆڵتی نزمی وێستگە لەگەڵ هەموو هێڵە هاتووەکان، لۆد، هێڵی خۆر و مۆلیدەکان. سەوز = کارەبای تێدایە، خۆڵەمێشی = بێ کارەبا. پچڕێنەر داخراو یان کراوە نیشان دەدرێت وەک کۆنترۆڵەرەکەی ڕایدەگەیەنێت. تیرەکان بە ئاراستەی ڕۆیشتنی وزە دەجووڵێن؛ خێراتر = وزەی زیاتر.", "Santralin AG barası; tüm girişler, yük, güneş fideri ve jeneratörler. Yeşil = enerjili, gri = enerjisiz. Kesici, kontrolörünün bildirdiği gibi açık veya kapalı çizilir. Oklar gücün aktığı yönde ilerler; hızlı = daha çok güç."],
    ["Sources stacked (solar, gensets, grid, battery), site load as a line. Grid export and battery charging are drawn below zero. Click a legend entry to hide it.", "المصادر مكدسة (الشمس، المولدات، الشبكة، البطارية)، والحمل كخط. التصدير إلى الشبكة وشحن البطارية تحت الصفر. انقر على عنصر في المفتاح لإخفائه.", "سەرچاوەکان لەسەر یەک (خۆر، مۆلیدە، گرید، پاتری)، لۆدی شوێن وەک هێڵێک. هەناردە بۆ گرید و بارگاویکردنی پاتری لە خوار سفرەوە نیشان دەدرێن. کرتە لە ناوێکی ڕێبەر بکە بۆ شاردنەوەی.", "Kaynaklar üst üste (güneş, jeneratörler, şebeke, batarya), tesis yükü çizgi olarak. Şebekeye verme ve batarya şarjı sıfırın altında. Gizlemek için açıklamadaki bir öğeye tıklayın."],
    ["Solar energy / site energy since midnight (plant time).", "الطاقة الشمسية / طاقة الموقع منذ منتصف الليل (توقيت المحطة).", "سۆلەر / وزەی شوێن لە نیوەشەوەوە (کاتی وێستگە).", "Gece yarısından beri güneş enerjisi / tesis enerjisi (santral saati)."],
    ["The gensets' fuel is not read on this plant; the litres are an estimate.", "لا يُقرأ وقود المولدات في هذه المحطة؛ اللترات تقديرية.", "سووتەمەنی مۆلیدەکان لەم وێستگەیە ناخوێندرێتەوە؛ لیترەکان خەمڵاندنن.", "Bu santralde jeneratör yakıtı okunmuyor; litreler tahminidir."],
    ["Diesel the gensets would have burnt today without the solar energy (estimate).", "الديزل الذي كانت المولدات ستحرقه اليوم بدون الطاقة الشمسية (تقدير).", "ئەو دیزڵەی مۆلیدەکان ئەمڕۆ بەبێ سۆلەر دەیانسووتاند (خەمڵاندن).", "Güneş enerjisi olmasaydı jeneratörlerin bugün yakacağı dizel (tahmin)."],
    ["link lost", "انقطع الاتصال", "پەیوەندی پچڕا", "bağlantı koptu"],
    ["Energy in each hour since midnight, by source. The line is the site energy.", "الطاقة في كل ساعة منذ منتصف الليل حسب المصدر. الخط هو طاقة الموقع.", "وزە لە هەر کاتژمێرێکدا لە نیوەشەوەوە، بەپێی سەرچاوە. هێڵەکە وزەی شوێنە.", "Gece yarısından beri her saatin enerjisi, kaynağa göre. Çizgi tesis enerjisidir."],
    ["Energy per day by source; the dotted line is the solar share of the day (right axis).", "الطاقة اليومية حسب المصدر؛ الخط المنقط هو حصة الشمس من اليوم (المحور الأيمن).", "وزەی هەر ڕۆژێک بەپێی سەرچاوە؛ هێڵە خاڵدارەکە بەشی خۆرە لەو ڕۆژە (تەوەری ڕاست).", "Kaynağa göre günlük enerji; noktalı çizgi günün güneş payıdır (sağ eksen)."],
    ["Average site load per hour of the day (columns) over the last 7 days (rows). Hover a cell for its value.", "متوسط الحمل لكل ساعة من اليوم (أعمدة) خلال آخر 7 أيام (صفوف). مرّر فوق خلية لرؤية قيمتها.", "تێکڕای لۆدی شوێن بۆ هەر کاتژمێرێکی ڕۆژ (ستوون) لە دوایین 7 ڕۆژدا (ڕیز). ماوس بخەرە سەر خانەیەک بۆ بەهاکەی.", "Son 7 günde (satırlar) günün her saati için ortalama tesis yükü (sütunlar). Değeri için bir hücrenin üzerine gelin."],
    ["Highest and lowest values in the selected time range, from the readings.", "أعلى وأدنى القيم في الفترة المختارة، من القراءات.", "بەرزترین و نزمترین بەها لە ماوەی هەڵبژێردراودا، لە خوێندنەوەکانەوە.", "Seçilen zaman aralığındaki en yüksek ve en düşük değerler, okumalardan."],
    ["Click a genset for all its values and a trend.", "انقر على مولد لعرض كل قيمه ومنحناه.", "کرتە لە مۆلیدەیەک بکە بۆ هەموو بەهاکان و گراف.", "Tüm değerleri ve eğilimi için bir jeneratöre tıklayın."],
    ["Gensets power / capacity of the gensets on the bus. Long under ~30 % is wet-stacking territory.", "قدرة المولدات / سعة المولدات على القضيب. البقاء طويلاً تحت ~30 % يسبب الترسبات الرطبة.", "توانای مۆلیدەکان / تواناى مۆلیدەکانی سەر باس. ماوەیەکی درێژ لە خوار ~30 % دەبێتە هۆی کۆبوونەوەی سووتەمەنی نەسووتاو.", "Jeneratör gücü / baradaki jeneratör kapasitesi. Uzun süre ~%30 altında kalmak ıslak yığılmaya yol açar."],
    ["How long each genset ran in the selected time range, and how often it was started.", "مدة تشغيل كل مولد في الفترة المختارة وعدد مرات تشغيله.", "هەر مۆلیدەیەک چەند کاری کرد لە ماوەی هەڵبژێردراودا، و چەند جار دەستی پێکرا.", "Seçilen aralıkta her jeneratörün ne kadar çalıştığı ve kaç kez başlatıldığı."],
    ["Green = as it should be.", "الأخضر = كما يجب.", "سەوز = وەک پێویستە.", "Yeşil = olması gerektiği gibi."],
    ["Green = not active. Red = active now.", "الأخضر = غير نشط. الأحمر = نشط الآن.", "سەوز = چالاک نییە. سوور = ئێستا چالاکە.", "Yeşil = etkin değil. Kırmızı = şu an etkin."],
    ["What the inverters could deliver now if nothing held them back.", "ما يمكن للعواكس تقديمه الآن لو لم يقيدها شيء.", "ئەوەی ئینڤێرتەرەکان ئێستا دەیانتوانی بیدەن ئەگەر هیچ شتێک ڕێگری نەکردایە.", "Hiçbir şey kısıtlamasaydı eviricilerin şu an verebileceği güç."],
    ["The gap is solar left unused: curtailment by the master while on gensets, tripped inverters, gateway stalls.", "الفجوة هي طاقة شمسية غير مستخدمة: تقليص من المتحكم الرئيسي أثناء تشغيل المولدات، عواكس متوقفة، توقف البوابة.", "بۆشاییەکە خۆری بەکارنەهاتووە: کەمکردنەوە لەلایەن کۆنترۆڵەری سەرەکی لە کاتی مۆلیدەدا، ئینڤێرتەری وەستاو، وەستانی دەروازە.", "Aradaki fark kullanılmayan güneştir: jeneratördeyken ana kontrolörün kısması, devre dışı eviriciler, ağ geçidi duraklamaları."],
    ["Click an inverter for all its values and a trend.", "انقر على عاكس لعرض كل قيمه ومنحناه.", "کرتە لە ئینڤێرتەرێک بکە بۆ هەموو بەهاکان و گراف.", "Tüm değerleri ve eğilimi için bir eviriciye tıklayın."],
    ["Ranked, and compared with the fleet average. An inverter far under its neighbours on a sunny day has a string, fuse or fan problem.", "مرتبة ومقارنة بالمتوسط. العاكس الأقل بكثير من جيرانه في يوم مشمس لديه مشكلة في السلسلة أو المصهر أو المروحة.", "ڕیزکراو و بەراورد بە تێکڕا. ئینڤێرتەرێک کە لە ڕۆژێکی خۆرەتاودا زۆر لە دراوسێکانی کەمترە کێشەی ستڕینگ، فیوز یان پانکەی هەیە.", "Sıralı ve filo ortalamasıyla karşılaştırmalı. Güneşli günde komşularının çok altındaki evirici dizi, sigorta veya fan sorunu yaşıyordur."],
    ["Over the selected time range. In band = 49.8 - 50.2 Hz.", "خلال الفترة المختارة. ضمن النطاق = 49.8 - 50.2 Hz.", "لە ماوەی هەڵبژێردراودا. لە سنووردا = 49.8 - 50.2 Hz.", "Seçilen aralıkta. Bant içi = 49.8 - 50.2 Hz."],
    ["Over the selected time range. In band = nominal +/- 5 %.", "خلال الفترة المختارة. ضمن النطاق = الاسمي +/- 5 %.", "لە ماوەی هەڵبژێردراودا. لە سنووردا = ئاسایی +/- 5 %.", "Seçilen aralıkta. Bant içi = nominal +/- %5."],
    ["How the devices answered their last read.", "كيف استجابت الأجهزة لآخر قراءة.", "ئامێرەکان چۆن وەڵامی دوایین خوێندنەوەیان دایەوە.", "Cihazların son okumaya nasıl yanıt verdiği."],
    ["Green = the device answered. Red = no valid data.", "الأخضر = استجاب الجهاز. الأحمر = لا بيانات صالحة.", "سەوز = ئامێرەکە وەڵامی دایەوە. سوور = داتای دروست نییە.", "Yeşil = cihaz yanıt verdi. Kırmızı = geçerli veri yok."],
    ["Search the events…", "ابحث في الأحداث…", "گەڕان لە ڕووداوەکان…", "Olaylarda ara…"],
    ["Filter by device or point…", "تصفية حسب الجهاز أو النقطة…", "فلتەر بەپێی ئامێر یان خاڵ…", "Cihaza veya noktaya göre filtrele…"],
    ["Pick a date", "اختر تاريخاً", "بەروارێک هەڵبژێرە", "Bir tarih seçin"],
    ["Close", "إغلاق", "داخستن", "Kapat"],
    ["YYYY-MM-DD HH:MM", "سنة-شهر-يوم ساعة:دقيقة", "ساڵ-مانگ-ڕۆژ کاتژمێر:خولەک", "YYYY-AA-GG SS:DD"],
    ["YYYY-MM-DD", "سنة-شهر-يوم", "ساڵ-مانگ-ڕۆژ", "YYYY-AA-GG"],
    ["Back to the dashboard", "العودة إلى لوحة المراقبة", "گەڕانەوە بۆ داشبۆرد", "Panoya dön"],
    ["Day before", "اليوم السابق", "ڕۆژی پێشوو", "Önceki gün"],
    ["Day after", "اليوم التالي", "ڕۆژی دواتر", "Sonraki gün"],
    ["today", "اليوم", "ئەمڕۆ", "bugün"],
    ["yesterday", "أمس", "دوێنێ", "dün"],
    ["this week", "هذا الأسبوع", "ئەم هەفتەیە", "bu hafta"],
    ["this month", "هذا الشهر", "ئەم مانگە", "bu ay"],
    ["last month", "الشهر الماضي", "مانگی ڕابردوو", "geçen ay"],
    ["last 24 hours", "آخر 24 ساعة", "دوایین 24 کاتژمێر", "son 24 saat"],
    ["alarm cleared", "زال الإنذار", "ئاگاداری نەما", "alarm temizlendi"],
    ["alarm active", "إنذار نشط", "ئاگاداری چالاکە", "alarm etkin"],
    ["data restored", "عادت البيانات", "داتا گەڕایەوە", "veri geri geldi"],
    ["no valid data (comms timeout)", "لا بيانات صالحة (انتهت مهلة الاتصال)", "داتای دروست نییە (کاتی پەیوەندی تەواو بوو)", "geçerli veri yok (iletişim zaman aşımı)"],
    ["grid back", "عادت الشبكة", "گرید گەڕایەوە", "şebeke geri geldi"],
    ["grid not available", "الشبكة غير متاحة", "گرید بەردەست نییە", "şebeke yok"],
    ["not answering", "لا يستجيب", "وەڵام ناداتەوە", "yanıt vermiyor"],
    ["controller alarm active", "إنذار المتحكم نشط", "ئاگاداری کۆنترۆڵەر چالاکە", "kontrolör alarmı etkin"],
    ["Mains/Bus healthy - transfer back to mains", "الشبكة/القضيب سليم - العودة إلى الشبكة", "گرید/باس تەندروستە - گەڕانەوە بۆ گرید", "Şebeke/bara sağlıklı - şebekeye geri geçiş"],
    ["Alarm Mains Fail - gensets take the load", "إنذار انقطاع الشبكة - المولدات تحمل الحمل", "ئاگاداری پچڕانی گرید - مۆلیدەکان لۆدەکە هەڵدەگرن", "Şebeke arızası alarmı - yükü jeneratörler alıyor"],
    ["back on the grid", "عاد إلى الشبكة", "گەڕایەوە سەر گرید", "şebekeye geri döndü"],
    ["tripped (grid over-voltage)", "فصل (جهد زائد في الشبكة)", "وەستا (ڤۆڵتی زیادی گرید)", "devre dışı (şebeke aşırı gerilim)"],
    ["comms timeout", "انتهت مهلة الاتصال", "کاتی پەیوەندی تەواو بوو", "iletişim zaman aşımı"],
    ["ComAp InteliNeo 5500 (site master)", "ComAp InteliNeo 5500 (المتحكم الرئيسي للموقع)", "ComAp InteliNeo 5500 (کۆنترۆڵەری سەرەکی)", "ComAp InteliNeo 5500 (saha ana kontrolörü)"],
    ["Sineng SP-320K-H string inverter, 320 kW, 12 strings", "عاكس سلسلة Sineng SP-320K-H، 320 kW، 12 سلسلة", "ئینڤێرتەری ستڕینگی Sineng SP-320K-H، 320 kW، 12 ستڕینگ", "Sineng SP-320K-H dizi evirici, 320 kW, 12 dizi"],
    ["Sineng SAU-100 virtual array (10 x SP-320K-H, 3.2 MW)", "مصفوفة Sineng SAU-100 الافتراضية (10 x SP-320K-H، 3.2 MW)", "کۆمەڵەی ڤێرچواڵی Sineng SAU-100 (10 x SP-320K-H، 3.2 MW)", "Sineng SAU-100 sanal dizi (10 x SP-320K-H, 3.2 MW)"],
    ["Solar covered", "غطت الطاقة الشمسية", "خۆر دابینی کرد", "Güneş karşıladı:"],
    ["of the site's energy. The grid was available", "من طاقة الموقع. كانت الشبكة متاحة", "لە وزەی شوێن. گرید بەردەست بوو", "tesis enerjisinin. Şebeke mevcuttu:"],
    ["of the site's energy. The government grid was available", "من طاقة الموقع. كانت الشبكة متاحة", "لە وزەی شوێن. گرید بەردەست بوو", "tesis enerjisinin. Şebeke mevcuttu:"],
    ["; the gensets ran", "؛ وعملت المولدات", "؛ مۆلیدەکان کاریان کرد", "; jeneratörler çalıştı:"],
    [". There were", ". وكان هناك", ". هەبوو", ". Olanlar:"],
    ["(NEO, SAU).", "(NEO، SAU).", "(NEO، SAU).", "(NEO, SAU)."],
    ["with", "مع", "لەگەڵ", "ile"],
    ["of the site energy", "من طاقة الموقع", "لە وزەی شوێن", "tesis enerjisinin"],
    ["Solar held back (curtailment, trips):", "الطاقة الشمسية المحجوبة (تقليص، فصل):", "خۆری ڕاگیراو (کەمکردنەوە، وەستان):", "Tutulan güneş (kısıtlama, devre dışı):"],
    ["energy per hour, kWh", "الطاقة لكل ساعة، kWh", "وزە بۆ هەر کاتژمێرێک، kWh", "saatlik enerji, kWh"],
    ["grid off / fault", "الشبكة مقطوعة / عطل", "گرید نییە / هەڵە", "şebeke yok / arıza"],
    ["genset running", "مولد يعمل", "مۆلیدە کار دەکات", "jeneratör çalışıyor"],
    ["solar producing", "الشمس تنتج", "خۆر بەرهەم دەهێنێت", "güneş üretiyor"],
    ["SAU answering", "SAU يستجيب", "SAU وەڵام دەداتەوە", "SAU yanıt veriyor"],
    ["Frequency min / avg / max", "التردد أدنى / متوسط / أعلى", "لەرەلەر کەمترین / ناوەند / زۆرترین", "Frekans min / ort / maks"],
    ["Voltage min / avg / max", "الجهد أدنى / متوسط / أعلى", "ڤۆڵت کەمترین / ناوەند / زۆرترین", "Gerilim min / ort / maks"],
    ["Devices answering now", "الأجهزة المستجيبة الآن", "ئامێرە وەڵامدەرەوەکان ئێستا", "Şu an yanıt veren cihazlar"],
    ["Solar data lost — the SAU gateway stopped answering", "فُقدت بيانات الطاقة الشمسية — توقفت بوابة SAU عن الاستجابة", "داتای خۆر ون بوو — دەروازەی SAU وەڵامی نەدایەوە", "Güneş verisi kayboldu — SAU ağ geçidi yanıt vermedi"],
    ["Government grid lost — the gensets took the plant", "انقطعت الشبكة — تولت المولدات المحطة", "گرید پچڕا — مۆلیدەکان وێستگەکەیان هەڵگرت", "Şebeke kesildi — santrali jeneratörler aldı"],
    ["Government grid lost — no genset took over", "انقطعت الشبكة — لم يتولَّ أي مولد", "گرید پچڕا — هیچ مۆلیدەیەک جێگەی نەگرتەوە", "Şebeke kesildi — hiçbir jeneratör devralmadı"],
    ["Government grid", "الشبكة", "گرید", "Şebeke"],
    ["government grid", "الشبكة", "گرید", "şebeke"],
    ["Controller alarm active", "إنذار المتحكم نشط", "ئاگاداری کۆنترۆڵەر چالاکە", "Kontrolör alarmı etkin"],
    ["of the day", "من اليوم", "لە ڕۆژەکە", "günün"],
    ["controller alarm active (while the SAU was silent)", "إنذار المتحكم نشط (أثناء صمت SAU)", "ئاگاداری کۆنترۆڵەر چالاک بوو (لە کاتی بێدەنگی SAU)", "kontrolör alarmı etkin (SAU sessizken)"],
    ["The inverters behind it could not be read during these stalls; that is the SAU's fault, not theirs.", "لم تُقرأ العواكس خلفها أثناء هذه التوقفات؛ الخلل في SAU وليس فيها.", "ئینڤێرتەرەکانی پشتی لەم وەستانانەدا نەخوێندرانەوە؛ هەڵەکە هی SAU ە نەک ئەوان.", "Bu duraklamalarda arkasındaki eviriciler okunamadı; kusur onların değil SAU'nundur."],
    ["Outage", "انقطاع", "پچڕان", "Kesinti"],
    ["From", "من", "لە", "Başlangıç"],
    ["To", "إلى", "بۆ", "Bitiş"],
    ["Duration", "المدة", "ماوە", "Süre"],
    ["Exact times", "أوقات دقيقة", "کاتی ورد", "Kesin saatler"],
    // overview mix, display settings, full screen
    ["Island", "منعزل", "جیاکراوە", "Ada"], ["Parallel", "متوازٍ", "هاوتەریب", "Paralel"], ["Exporting to the grid", "تصدير إلى الشبكة", "ناردن بۆ گرید", "Şebekeye veriliyor"],
    ["To the grid", "إلى الشبكة", "بۆ گرید", "Şebekeye"], ["Battery charging", "البطارية تشحن", "پاتری بارگاوی دەبێت", "Batarya şarj oluyor"], ["Plant state unknown", "حالة المحطة غير معروفة", "دۆخی وێستگە نەزانراوە", "Santral durumu bilinmiyor"],
    ["solar idle", "الطاقة الشمسية متوقفة", "خۆر بێکارە", "güneş boşta"], ["grid on", "الشبكة متاحة", "گرید هەیە", "şebeke var"], ["solar flowing out to the grid", "الطاقة الشمسية تتدفق إلى الشبكة", "خۆر دەڕوات بۆ گرید", "güneş şebekeye akıyor"],
    ["Display", "العرض", "پیشاندان", "Görünüm"], ["kept on this computer", "محفوظ على هذا الحاسوب", "لەسەر ئەم کۆمپیوتەرە هەڵدەگیرێت", "bu bilgisayarda saklanır"],
    ["Every page, the alarms and the day report.", "كل الصفحات والإنذارات والتقرير اليومي.", "هەموو پەڕەکان، ئاگادارکردنەوەکان و ڕاپۆرتی ڕۆژانە.", "Tüm sayfalar, alarmlar ve günlük rapor."],
    ["Arabic", "العربية", "عەرەبی", "Arapça"], ["Kurdish · Sorani", "الكردية · السورانية", "کوردی · سۆرانی", "Kürtçe · Sorani"], ["Turkish", "التركية", "تورکی", "Türkçe"], ["English", "الإنجليزية", "ئینگلیزی", "İngilizce"],
    ["Text size", "حجم النص", "قەبارەی نووسین", "Yazı boyutu"], ["Larger text for a wall screen or a screen far away.", "نص أكبر لشاشة حائط أو شاشة بعيدة.", "نووسینی گەورەتر بۆ شاشەی دیوار یان شاشەی دوور.", "Duvar ekranı veya uzaktaki ekran için daha büyük yazı."],
    ["Normal", "عادي", "ئاسایی", "Normal"], ["Large", "كبير", "گەورە", "Büyük"], ["for a desk", "للمكتب", "بۆ مێز", "masa için"], ["for a wall screen", "لشاشة الحائط", "بۆ شاشەی دیوار", "duvar ekranı için"],
    ["TV mode", "وضع التلفاز", "دۆخی تەلەفزیۆن", "TV modu"], ["Start TV mode", "بدء وضع التلفاز", "دەستپێکردنی دۆخی تەلەفزیۆن", "TV modunu başlat"], ["Leave TV mode", "الخروج من وضع التلفاز", "دەرچوون لە دۆخی تەلەفزیۆن", "TV modundan çık"],
    ["Panels only, without the menu and the top buttons, for a screen in the control room. Press Esc to leave.", "اللوحات فقط، بدون القائمة والأزرار العلوية، لشاشة غرفة التحكم. اضغط Esc للخروج.", "تەنها پانێڵەکان، بەبێ مێنیو و دوگمەکانی سەرەوە، بۆ شاشەی ژووری کۆنترۆڵ. Esc دابگرە بۆ دەرچوون.", "Yalnızca paneller, menü ve üst düğmeler olmadan, kontrol odası ekranı için. Çıkmak için Esc."],
    ["to leave", "للخروج", "بۆ دەرچوون", "çıkmak için"], ["to leave full screen", "للخروج من ملء الشاشة", "بۆ دەرچوون لە پڕی شاشە", "tam ekrandan çıkmak için"], ["Leave full screen", "الخروج من ملء الشاشة", "دەرچوون لە پڕی شاشە", "Tam ekrandan çık"],
    ["Larger text", "نص أكبر", "نووسینی گەورەتر", "Daha büyük yazı"], ["Normal text", "نص عادي", "نووسینی ئاسایی", "Normal yazı"], ["Display settings", "إعدادات العرض", "ڕێکخستنی پیشاندان", "Görünüm ayarları"],
    // navigation and top bar
    ["Plant", "المحطة", "وێستگە", "Santral"], ["Operate", "التشغيل", "کارپێکردن", "İşletme"], ["Home", "الرئيسية", "سەرەکی", "Ana sayfa"],
    ["Overview", "نظرة عامة", "پوختە", "Genel bakış"], ["Gensets", "المولدات", "مۆلیدەکان", "Jeneratörler"], ["Master controller", "المتحكم الرئيسي", "کۆنترۆڵەری سەرەکی", "Ana kontrolör"],
    ["Solar PV", "الطاقة الشمسية", "سۆلەر", "Güneş enerjisi"], ["Mains & grid", "الشبكة والمغذيات", "گرید و هێڵەکان", "Şebeke"], ["Power quality", "جودة الطاقة", "کوالیتی وزە", "Güç kalitesi"],
    ["Devices", "الأجهزة", "ئامێرەکان", "Cihazlar"], ["Alarms & events", "الإنذارات والأحداث", "ئاگادارکردنەوە و ڕووداوەکان", "Alarmlar ve olaylar"], ["Export", "تصدير", "هەناردن", "Dışa aktar"],
    ["Day report", "التقرير اليومي", "ڕاپۆرتی ڕۆژانە", "Günlük rapor"], ["All points", "جميع النقاط", "هەموو خاڵەکان", "Tüm noktalar"], ["Settings", "الإعدادات", "ڕێکخستنەکان", "Ayarlar"],
    ["live", "مباشر", "ڕاستەوخۆ", "canlı"], ["read-only", "قراءة فقط", "تەنها خوێندنەوە", "salt okunur"], ["demo data", "بيانات تجريبية", "داتای تاقیکردنەوە", "demo verisi"],
    ["connecting", "جارٍ الاتصال", "پەیوەندی دەگرێت", "bağlanıyor"], ["collector not reachable", "لا يمكن الوصول إلى المجمّع", "کۆکەرەوە بەردەست نییە", "toplayıcıya ulaşılamıyor"],
    ["Search…", "بحث…", "گەڕان…", "Ara…"], ["Read devices now", "اقرأ الأجهزة الآن", "ئێستا ئامێرەکان بخوێنەوە", "Cihazları şimdi oku"], ["Time range", "الفترة الزمنية", "ماوەی کات", "Zaman aralığı"],
    ["CCI Plant Monitoring", "مراقبة محطة CCI", "چاودێری وێستگەی CCI", "CCI Santral İzleme"],
    ["Read-only: this program never writes to a device.", "قراءة فقط: هذا البرنامج لا يكتب أبداً إلى أي جهاز.", "تەنها خوێندنەوە: ئەم پرۆگرامە هەرگیز بۆ ئامێر نانووسێت.", "Salt okunur: bu program hiçbir cihaza yazmaz."],
    // sections
    ["Plant overview", "نظرة عامة على المحطة", "پوختەی وێستگە", "Santral genel bakış"], ["bus frequency and voltage", "تردد وجهد القضبان", "لەرەلەر و ڤۆڵتی باس", "bara frekansı ve gerilimi"],
    ["click one for all its values and a trend", "انقر على أحدها لعرض كل قيمه ومنحناه", "کرتە لە یەکێک بکە بۆ هەموو بەهاکان و گراف", "tüm değerleri ve grafiği için birine tıklayın"],
    ["Excel and CSV", "إكسل و CSV", "ئێکسڵ و CSV", "Excel ve CSV"], ["click a point for its trend", "انقر على نقطة لعرض منحناها", "کرتە لە خاڵێک بکە بۆ گرافەکەی", "grafiği için bir noktaya tıklayın"],
    // overview
    ["Site load", "حمل الموقع", "لۆدی شوێن", "Tesis yükü"], ["Grid import", "استيراد من الشبكة", "هاوردە لە گرید", "Şebekeden alınan"], ["Grid export", "تصدير إلى الشبكة", "هەناردە بۆ گرید", "Şebekeye verilen"],
    ["Battery", "البطارية", "پاتری", "Batarya"], ["Solar share now", "حصة الطاقة الشمسية الآن", "بەشی خۆر ئێستا", "Şu anki güneş payı"], ["Active faults", "الأعطال النشطة", "هەڵە چالاکەکان", "Aktif arızalar"],
    ["Live power flow", "تدفق الطاقة المباشر", "ڕۆیشتنی وزەی ڕاستەوخۆ", "Canlı güç akışı"], ["Power - where the load comes from", "الطاقة - مصدر الحمل", "وزە - لۆد لە کوێوە دێت", "Güç - yük nereden geliyor"],
    ["Solar share today", "حصة الطاقة الشمسية اليوم", "بەشی خۆر ئەمڕۆ", "Bugünkü güneş payı"], ["Energy today by source", "طاقة اليوم حسب المصدر", "وزەی ئەمڕۆ بەپێی سەرچاوە", "Kaynağa göre bugünkü enerji"],
    ["Fuel today", "الوقود اليوم", "سووتەمەنی ئەمڕۆ", "Bugünkü yakıt"], ["Genset energy today", "طاقة المولدات اليوم", "وزەی مۆلیدەکان ئەمڕۆ", "Bugünkü jeneratör enerjisi"],
    ["Fuel avoided by solar", "الوقود الموفَّر بالطاقة الشمسية", "سووتەمەنی پاشەکەوتکراو بە خۆر", "Güneş sayesinde tasarruf edilen yakıt"], ["Grid today", "الشبكة اليوم", "گرید ئەمڕۆ", "Bugün şebeke"],
    ["Plant state", "حالة المحطة", "دۆخی وێستگە", "Santral durumu"], ["Today hour by hour", "اليوم ساعة بساعة", "ئەمڕۆ کاتژمێر بە کاتژمێر", "Bugün saat saat"], ["Last 7 days", "آخر 7 أيام", "دوایین ٧ ڕۆژ", "Son 7 gün"],
    ["Site load heatmap", "خريطة حرارية لحمل الموقع", "نەخشەی گەرمی لۆدی شوێن", "Tesis yükü ısı haritası"], ["Peaks today", "القمم اليوم", "لووتکەکانی ئەمڕۆ", "Bugünkü tepeler"],
    ["Government grid", "الشبكة الحكومية", "گرید", "Devlet şebekesi"], ["Solar", "الشمسية", "خۆر", "Güneş"], ["Grid", "الشبكة", "گرید", "Şebeke"], ["Device links", "اتصالات الأجهزة", "پەیوەندی ئامێرەکان", "Cihaz bağlantıları"],
    ["Site energy", "طاقة الموقع", "وزەی شوێن", "Tesis enerjisi"], ["Solar share", "حصة الطاقة الشمسية", "بەشی خۆر", "Güneş payı"], ["Site load", "حمل الموقع", "لۆدی شوێن", "Tesis yükü"],
    ["Fuel cost avoided", "تكلفة الوقود الموفَّرة", "تێچووی سووتەمەنی پاشەکەوتکراو", "Tasarruf edilen yakıt maliyeti"], ["Diesel avoided", "الديزل الموفَّر", "دیزڵی پاشەکەوتکراو", "Tasarruf edilen dizel"],
    ["CO₂ avoided", "ثاني أكسيد الكربون المُتجنَّب", "CO₂ ی ڕێگیراو", "Önlenen CO₂"], ["today, thanks to solar", "اليوم، بفضل الطاقة الشمسية", "ئەمڕۆ، بەهۆی خۆرەوە", "bugün, güneş sayesinde"],
    ["Island operation — the gensets carry the plant", "تشغيل منعزل — المولدات تغذي المحطة", "کارکردنی جیاکراوە — مۆلیدەکان وێستگە هەڵدەگرن", "Ada modu — santrali jeneratörler taşıyor"],
    ["Grid off and no genset on line", "الشبكة مقطوعة ولا مولد في الخدمة", "گرید کوژاوە و هیچ مۆلیدێک کار ناکات", "Şebeke yok ve devrede jeneratör yok"],
    ["Parallel — grid and gensets together", "تشغيل متوازٍ — الشبكة والمولدات معاً", "هاوتەریب — گرید و مۆلیدەکان پێکەوە", "Paralel — şebeke ve jeneratörler birlikte"],
    ["Grid operation — government supply", "تشغيل على الشبكة — التغذية الحكومية", "کارکردن لەسەر گرید — گرید", "Şebeke modu — devlet beslemesi"],
    ["Gensets on line", "المولدات في الخدمة", "مۆلیدەکان کار دەکەن", "Jeneratörler devrede"],
    // flow
    ["SOLAR PV", "الطاقة الشمسية", "سۆلەر", "GÜNEŞ"], ["GENSETS", "المولدات", "مۆلیدەکان", "JENERATÖRLER"], ["GRID", "الشبكة", "گرید", "ŞEBEKE"],
    ["PLANT LOAD", "حمل المحطة", "لۆدی وێستگە", "SANTRAL YÜKÜ"], ["BATTERY", "البطارية", "پاتری", "BATARYA"], ["LV BUS · Hz", "قضبان الجهد المنخفض · هرتز", "باسی ڤۆڵتی نزم · Hz", "AG BARA · Hz"],
    ["government grid OFF", "الشبكة الحكومية مقطوعة", "گرید کوژاوەتەوە", "devlet şebekesi KAPALI"], ["government grid ON", "الشبكة الحكومية متاحة", "گرید هەیە", "devlet şebekesi AÇIK"],
    ["government grid not available", "الشبكة الحكومية غير متاحة", "گرید بەردەست نییە", "devlet şebekesi yok"], ["government grid on", "الشبكة الحكومية متاحة", "گرید هەیە", "devlet şebekesi açık"], ["exporting", "تصدير", "هەناردن", "veriliyor"],
    // gensets and master
    ["Genset loading", "تحميل المولدات", "لۆدی مۆلیدەکان", "Jeneratör yüklenmesi"], ["Capacity and reserves", "السعة والاحتياطي", "توانا و یەدەگ", "Kapasite ve yedekler"],
    ["Capacity on line", "السعة في الخدمة", "توانای کارا", "Devredeki kapasite"], ["Spinning reserve", "الاحتياطي الدوّار", "یەدەگی سووڕاو", "Dönen yedek"], ["Start one below", "تشغيل مولد تحت", "دەستپێکردنی یەکێک لە خوار", "Altında bir tane başlat"],
    ["Stop one above", "إيقاف مولد فوق", "وەستاندنی یەکێک لە سەرووی", "Üstünde bir tane durdur"], ["Hours · starts", "الساعات · مرات التشغيل", "کاتژمێر · دەستپێکردن", "Saat · çalıştırma"],
    ["Which genset ran when", "أي مولد عمل ومتى", "کام مۆلیدە کەی کاری کرد", "Hangi jeneratör ne zaman çalıştı"], ["Run time and starts", "مدة التشغيل ومرات البدء", "ماوەی کارکردن و دەستپێکردن", "Çalışma süresi ve çalıştırmalar"],
    ["Fuel level", "مستوى الوقود", "ئاستی سووتەمەنی", "Yakıt seviyesi"], ["Fuel", "الوقود", "سووتەمەنی", "Yakıt"], ["Gensets - power, capacity on line, reserve", "المولدات - القدرة والسعة والاحتياطي", "مۆلیدەکان - وزە، توانا، یەدەگ", "Jeneratörler - güç, kapasite, yedek"],
    ["Gensets - power per unit", "المولدات - القدرة لكل وحدة", "مۆلیدەکان - وزە بۆ هەر یەکە", "Jeneratörler - ünite başına güç"], ["Gensets now", "المولدات الآن", "مۆلیدەکان ئێستا", "Jeneratörler şimdi"],
    ["Breaker (GCB)", "القاطع (GCB)", "پچڕێنەر (GCB)", "Kesici (GCB)"], ["On CAN bus", "على ناقل CAN", "لەسەر CAN", "CAN hattında"], ["closed", "مغلق", "داخراو", "kapalı"], ["open", "مفتوح", "کراوە", "açık"],
    ["present", "موجود", "هەیە", "mevcut"], ["MISSING", "مفقود", "نییە", "YOK"], ["loaded", "محمّل", "بارکراو", "yüklü"], ["standby", "استعداد", "ئامادە", "beklemede"], ["running", "يعمل", "کار دەکات", "çalışıyor"],
    ["alarm", "إنذار", "ئاگاداری", "alarm"], ["no data", "لا بيانات", "داتا نییە", "veri yok"], ["Coolant", "سائل التبريد", "ساردکەرەوە", "Soğutma suyu"], ["Oil pressure", "ضغط الزيت", "پەستانی ڕۆن", "Yağ basıncı"],
    ["Fuel rate", "معدل الوقود", "ڕێژەی سووتەمەنی", "Yakıt tüketimi"], ["Run hours", "ساعات التشغيل", "کاتژمێرەکانی کارکردن", "Çalışma saati"], ["Mode", "الوضع", "دۆخ", "Mod"],
    ["Breaker state", "حالة القواطع", "دۆخی پچڕێنەرەکان", "Kesici durumu"], ["Ethernet", "إيثرنت", "ئیثەرنێت", "Ethernet"], ["Controller battery", "بطارية المتحكم", "پاتری کۆنترۆڵەر", "Kontrolör aküsü"],
    ["Gensets hours · starts", "ساعات المولدات · مرات التشغيل", "کاتژمێری مۆلیدەکان · دەستپێکردن", "Jeneratör saatleri · çalıştırma"], ["Healthy signals", "إشارات سليمة", "ئاماژە تەندروستەکان", "Sağlık sinyalleri"],
    ["Alarm signals", "إشارات الإنذار", "ئاماژەکانی ئاگاداری", "Alarm sinyalleri"], ["System ready", "النظام جاهز", "سیستەم ئامادەیە", "Sistem hazır"], ["Bus healthy", "القضبان سليمة", "باس تەندروستە", "Bara sağlıklı"],
    ["Mains available", "الشبكة متاحة", "گرید بەردەستە", "Şebeke mevcut"], ["PV operable", "الطاقة الشمسية جاهزة", "خۆر ئامادەیە", "PV çalışabilir"], ["PV breaker", "قاطع الطاقة الشمسية", "پچڕێنەری خۆر", "PV kesicisi"],
    ["In AUTO", "على الوضع التلقائي", "لە دۆخی AUTO", "AUTO modunda"], ["Alarm / warning", "إنذار / تحذير", "ئاگاداری / هۆشداری", "Alarm / uyarı"], ["Shutdown", "إيقاف", "کوژاندنەوە", "Kapatma"],
    ["Mains fail", "انقطاع الشبكة", "پچڕانی گرید", "Şebeke arızası"], ["PV logger comm", "اتصال مسجّل الطاقة الشمسية", "پەیوەندی تۆمارکەری خۆر", "PV logger iletişimi"], ["Peripheral comm", "اتصال الأجهزة الطرفية", "پەیوەندی دەرەکی", "Çevre birim iletişimi"],
    ["Emergency stop", "إيقاف طوارئ", "وەستانی فریاکەوتن", "Acil durdurma"], ["OK", "سليم", "باشە", "TAMAM"], ["NOT OK", "غير سليم", "باش نییە", "SORUNLU"], ["ACTIVE", "نشط", "چالاکە", "AKTİF"],
    ["Island (gensets)", "منعزل (المولدات)", "جیاکراوە (مۆلیدەکان)", "Ada (jeneratörler)"], ["Mains operation", "التشغيل على الشبكة", "کارکردن لەسەر گرید", "Şebeke çalışması"], ["Parallel to mains", "متوازٍ مع الشبكة", "هاوتەریب لەگەڵ گرید", "Şebekeye paralel"],
    ["Connected", "متصل", "پەیوەستە", "Bağlı"], ["Disconnected", "غير متصل", "پەیوەست نییە", "Bağlı değil"],
    // solar
    ["Solar devices", "أجهزة الطاقة الشمسية", "ئامێرەکانی خۆر", "Güneş cihazları"], ["Inverters working", "العاكسات العاملة", "ئینڤێرتەرە کارکەرەکان", "Çalışan invertörler"],
    ["Available from the sun", "المتاح من الشمس", "بەردەست لە خۆرەوە", "Güneşten kullanılabilir"], ["Solar delivered", "الطاقة الشمسية المسلَّمة", "خۆری گەیەندراو", "Verilen güneş gücü"],
    ["Solar energy today", "الطاقة الشمسية اليوم", "سۆلەر ئەمڕۆ", "Bugünkü güneş enerjisi"], ["PV allowed by master", "الطاقة الشمسية المسموحة من المتحكم", "خۆری ڕێپێدراو لە کۆنترۆڵەرەوە", "Ana kontrolörün izin verdiği PV"],
    ["Irradiance", "الإشعاع", "تیشک", "Işınım"], ["Solar - available vs delivered", "الطاقة الشمسية - المتاح مقابل المسلَّم", "خۆر - بەردەست بەرامبەر گەیەندراو", "Güneş - kullanılabilir / verilen"],
    ["Inverters now", "العاكسات الآن", "ئینڤێرتەرەکان ئێستا", "İnvertörler şimdi"], ["Inverter output", "خرج العاكسات", "دەرچووی ئینڤێرتەر", "İnvertör çıkışı"], ["Inverter cabin temperature", "حرارة كابينة العاكس", "پلەی گەرمی ئینڤێرتەر", "İnvertör kabin sıcaklığı"],
    ["Inverter fleet", "أسطول العاكسات", "هەموو ئینڤێرتەرەکان", "İnvertör filosu"], ["Energy today per inverter", "طاقة اليوم لكل عاكس", "وزەی ئەمڕۆ بۆ هەر ئینڤێرتەرێک", "İnvertör başına bugünkü enerji"],
    ["Inverter", "العاكس", "ئینڤێرتەر", "İnvertör"], ["Status", "الحالة", "دۆخ", "Durum"], ["AC power", "قدرة AC", "وزەی AC", "AC güç"], ["Efficiency", "الكفاءة", "کارایی", "Verim"], ["Cabin", "الكابينة", "کابین", "Kabin"],
    ["Today kWh", "اليوم kWh", "ئەمڕۆ kWh", "Bugün kWh"], ["Total MWh", "الإجمالي MWh", "کۆ MWh", "Toplam MWh"], ["Link", "الاتصال", "پەیوەندی", "Bağlantı"], ["generating", "ينتج", "بەرهەم دەهێنێت", "üretiyor"],
    ["sleeping", "خامل", "خەوتووە", "uykuda"], ["fault", "عطل", "هەڵە", "arıza"], ["derated", "مخفَّض", "کەمکراوەتەوە", "kısıtlı"], ["lost", "مفقود", "پچڕاو", "kayıp"], ["online", "متصل", "سەرهێڵ", "çevrimiçi"],
    ["ONLINE", "متصل", "سەرهێڵ", "ÇEVRİMİÇİ"], ["NO DATA", "لا بيانات", "داتا نییە", "VERİ YOK"], ["producing", "ينتج", "بەرهەم دەهێنێت", "üretiyor"], ["idle", "خامل", "وەستاو", "boşta"],
    // mains, quality, devices
    ["Grid connection", "اتصال الشبكة", "پەیوەندی گرید", "Şebeke bağlantısı"], ["Grid availability", "توفر الشبكة", "بەردەستی گرید", "Şebeke durumu"], ["Battery state of charge", "حالة شحن البطارية", "ئاستی پڕبوونی پاتری", "Batarya doluluk"],
    ["breaker closed", "القاطع مغلق", "پچڕێنەر داخراوە", "kesici kapalı"], ["breaker open", "القاطع مفتوح", "پچڕێنەر کراوەیە", "kesici açık"], ["Load", "الحمل", "لۆد", "Yük"], ["Power factor", "معامل القدرة", "فاکتەری وزە", "Güç faktörü"],
    ["Voltage", "الجهد", "ڤۆڵت", "Gerilim"], ["Frequency", "التردد", "لەرەلەر", "Frekans"], ["Imported total", "إجمالي المستورد", "کۆی هاوردە", "Toplam alınan"],
    ["Bus frequency", "تردد القضبان", "لەرەلەری باس", "Bara frekansı"], ["Bus voltage", "جهد القضبان", "ڤۆڵتی باس", "Bara gerilimi"], ["Frequency statistics", "إحصائيات التردد", "ئاماری لەرەلەر", "Frekans istatistikleri"],
    ["Voltage statistics", "إحصائيات الجهد", "ئاماری ڤۆڵت", "Gerilim istatistikleri"], ["Minimum", "الأدنى", "کەمترین", "En düşük"], ["Average", "المتوسط", "ناوەند", "Ortalama"], ["Maximum", "الأقصى", "زۆرترین", "En yüksek"],
    ["Fleet health", "صحة الأجهزة", "تەندروستی ئامێرەکان", "Cihaz sağlığı"], ["Device links (every poll)", "اتصالات الأجهزة (كل قراءة)", "پەیوەندی ئامێرەکان (هەموو خوێندنەوەیەک)", "Cihaz bağlantıları (her okuma)"],
    ["Online", "متصل", "سەرهێڵ", "Çevrimiçi"], ["Stale", "قديمة", "کۆن", "Eski"], ["No data", "لا بيانات", "داتا نییە", "Veri yok"], ["Registers pending", "السجلات قيد الإعداد", "ڕێکخستن ماوە", "Kayıtlar bekliyor"],
    ["Peak load", "ذروة الحمل", "لووتکەی لۆد", "Tepe yük"], ["Peak solar", "ذروة الطاقة الشمسية", "لووتکەی خۆر", "Tepe güneş"], ["Peak gensets", "ذروة المولدات", "لووتکەی مۆلیدەکان", "Tepe jeneratör"],
    ["Peak grid import", "ذروة الاستيراد من الشبكة", "لووتکەی هاوردەی گرید", "Tepe şebeke alımı"], ["Lowest frequency", "أدنى تردد", "نزمترین لەرەلەر", "En düşük frekans"], ["Highest frequency", "أعلى تردد", "بەرزترین لەرەلەر", "En yüksek frekans"],
    // alarms
    ["Active now", "النشطة الآن", "چالاک ئێستا", "Şu an aktif"], ["Events over time", "الأحداث عبر الزمن", "ڕووداوەکان بەپێی کات", "Zaman içinde olaylar"], ["Event log", "سجل الأحداث", "تۆماری ڕووداوەکان", "Olay günlüğü"],
    ["All", "الكل", "هەموو", "Tümü"], ["Errors", "الأخطاء", "هەڵەکان", "Hatalar"], ["Warnings", "التحذيرات", "هۆشدارییەکان", "Uyarılar"], ["Info", "معلومات", "زانیاری", "Bilgi"],
    ["Error", "خطأ", "هەڵە", "Hata"], ["Warning", "تحذير", "هۆشداری", "Uyarı"], ["error", "خطأ", "هەڵە", "hata"], ["warning", "تحذير", "هۆشداری", "uyarı"], ["info", "معلومات", "زانیاری", "bilgi"],
    ["Time", "الوقت", "کات", "Zaman"], ["Severity", "الخطورة", "ئاست", "Önem"], ["Device", "الجهاز", "ئامێر", "Cihaz"], ["Message", "الرسالة", "پەیام", "Mesaj"],
    ["No active faults", "لا أعطال نشطة", "هیچ هەڵەیەکی چالاک نییە", "Aktif arıza yok"], ["No active alarms", "لا إنذارات نشطة", "هیچ ئاگادارییەکی چالاک نییە", "Aktif alarm yok"], ["Alarms", "الإنذارات", "ئاگادارییەکان", "Alarmlar"],
    ["Acknowledge", "إقرار", "پەسەندکردن", "Onayla"], ["Acknowledge all", "إقرار الكل", "پەسەندکردنی هەموو", "Tümünü onayla"], ["Details", "التفاصيل", "وردەکاری", "Ayrıntılar"],
    ["Alerts on this screen", "التنبيهات على هذه الشاشة", "ئاگادارکردنەوە لەم شاشەیە", "Bu ekrandaki uyarılar"], ["Alarm history · last 7 days", "سجل الإنذارات · آخر 7 أيام", "مێژووی ئاگادارییەکان · دوایین ٧ ڕۆژ", "Alarm geçmişi · son 7 gün"],
    ["Sound on a new alarm (repeats every minute until acknowledged)", "صوت عند إنذار جديد (يتكرر كل دقيقة حتى الإقرار)", "دەنگ بۆ ئاگاداری نوێ (هەر خولەکێک دووبارە دەبێتەوە تا پەسەند دەکرێت)", "Yeni alarmda ses (onaylanana kadar her dakika)"],
    ["Windows notification on a new alarm", "إشعار ويندوز عند إنذار جديد", "ئاگادارکردنەوەی ویندۆز بۆ ئاگاداری نوێ", "Yeni alarmda Windows bildirimi"],
    // export, points, settings
    ["Download a time window", "تنزيل فترة زمنية", "داگرتنی ماوەیەکی کات", "Bir zaman aralığını indir"], ["Reports and tag lists", "التقارير وقوائم الوسوم", "ڕاپۆرت و لیستی تاگەکان", "Raporlar ve etiket listeleri"],
    ["From", "من", "لە", "Başlangıç"], ["To", "إلى", "بۆ", "Bitiş"], ["Step", "الخطوة", "هەنگاو", "Adım"], ["Format", "الصيغة", "شێوە", "Biçim"], ["Download data", "تنزيل البيانات", "داگرتنی داتا", "Veriyi indir"],
    ["Last 24 h", "آخر 24 ساعة", "دوایین ٢٤ کاتژمێر", "Son 24 saat"], ["Last 30 days", "آخر 30 يوماً", "دوایین ٣٠ ڕۆژ", "Son 30 gün"], ["Report day (UTC)", "يوم التقرير (UTC)", "ڕۆژی ڕاپۆرت (UTC)", "Rapor günü (UTC)"],
    ["Open the day report (print / PDF)", "افتح التقرير اليومي (طباعة / PDF)", "ڕاپۆرتی ڕۆژانە بکەرەوە (چاپ / PDF)", "Günlük raporu aç (yazdır / PDF)"], ["Download daily report (.xlsx)", "تنزيل التقرير اليومي (.xlsx)", "داگرتنی ڕاپۆرتی ڕۆژانە (.xlsx)", "Günlük raporu indir (.xlsx)"],
    ["Tag list with live values (.xlsx)", "قائمة الوسوم بالقيم الحية (.xlsx)", "لیستی تاگەکان بە بەهای ڕاستەوخۆ (.xlsx)", "Canlı değerli etiket listesi (.xlsx)"], ["Tag list (.csv)", "قائمة الوسوم (.csv)", "لیستی تاگەکان (.csv)", "Etiket listesi (.csv)"],
    ["Point", "النقطة", "خاڵ", "Nokta"], ["Value", "القيمة", "بەها", "Değer"], ["Unit", "الوحدة", "یەکە", "Birim"], ["Quality", "الجودة", "کوالیتی", "Kalite"], ["Age", "العمر", "تەمەن", "Yaş"], ["Register", "السجل", "ڕیجستەر", "Kayıt"], ["Note", "ملاحظة", "تێبینی", "Not"],
    ["Polling", "القراءة الدورية", "خوێندنەوەی خولی", "Okuma"], ["About this collector", "حول هذا المجمّع", "دەربارەی ئەم کۆکەرەوەیە", "Bu toplayıcı hakkında"], ["Read the devices every", "اقرأ الأجهزة كل", "ئامێرەکان بخوێنەوە هەر", "Cihazları şu aralıkla oku"], ["Save", "حفظ", "پاشەکەوت", "Kaydet"],
    ["Language", "اللغة", "زمان", "Dil"],
    ["Simple", "مبسّط", "سادە", "Basit"], ["Detailed", "مفصّل", "ورد", "Ayrıntılı"], ["Single-line diagram", "المخطط أحادي الخط", "نەخشەی تاکهێڵ", "Tek hat şeması"],
    ["Live power flow", "تدفق الطاقة المباشر", "ڕۆیشتنی وزەی ڕاستەوخۆ", "Canlı güç akışı"], ["Energy & trends", "الطاقة والاتجاهات", "وزە و ڕەوتەکان", "Enerji ve eğilimler"],
    ["Plant load", "حمل المحطة", "لۆدی وێستگە", "Santral yükü"], ["LV BUSBAR", "قضبان الجهد المنخفض", "باسباری ڤۆڵتی نزم", "AG BARA"], ["CLOSED", "مغلق", "داخراو", "KAPALI"], ["OPEN", "مفتوح", "کراوە", "AÇIK"],
    ["STANDBY", "استعداد", "ئامادە", "BEKLEMEDE"], ["ON LINE", "في الخدمة", "کار دەکات", "DEVREDE"], ["PV LIMIT", "حد الطاقة الشمسية", "سنووری خۆر", "PV LİMİTİ"], ["Bus voltage", "جهد القضبان", "ڤۆڵتی باس", "Bara gerilimi"],
    ["Breakers", "القواطع", "پچڕێنەرەکان", "Kesiciler"],
    ["Today so far", "اليوم حتى الآن", "ئەمڕۆ تا ئێستا", "Bugün şu ana kadar"], ["Yesterday", "أمس", "دوێنێ", "Dün"], ["This month", "هذا الشهر", "ئەم مانگە", "Bu ay"],
    ["Custom export", "تصدير مخصص", "هەناردنی تایبەت", "Özel dışa aktarma"], ["Time window", "الفترة الزمنية", "ماوەی کات", "Zaman aralığı"], ["Resolution", "الدقة", "وردی", "Çözünürlük"],
    ["Your file", "ملفك", "فایلەکەت", "Dosyanız"], ["Download", "تنزيل", "داگرتن", "İndir"], ["Rows", "الصفوف", "ڕیزەکان", "Satır"], ["Columns", "الأعمدة", "ستوونەکان", "Sütun"], ["Size, about", "الحجم تقريباً", "قەبارە نزیکەی", "Boyut, yaklaşık"],
    ["Reports and lists", "التقارير والقوائم", "ڕاپۆرت و لیستەکان", "Raporlar ve listeler"], ["Daily Excel report", "تقرير إكسل اليومي", "ڕاپۆرتی ڕۆژانەی ئێکسڵ", "Günlük Excel raporu"],
    ["DT Turkey tag list", "قائمة وسوم DT Turkey", "لیستی تاگی DT Turkey", "DT Turkey etiket listesi"], ["What happened", "ما الذي حدث", "چی ڕوویدا", "Ne oldu"],
    ["fault", "عطل", "هەڵە", "arıza"], ["No faults and no warnings.", "لا أعطال ولا تحذيرات.", "هیچ هەڵە و هۆشدارییەک نییە.", "Arıza ve uyarı yok."],
    ["Click anywhere on the page once so the browser allows the alarm sound.", "انقر في أي مكان على الصفحة مرة واحدة ليسمح المتصفح بصوت الإنذار.", "یەک جار لە هەر شوێنێکی پەڕەکە کرتە بکە بۆ ئەوەی وێبگەڕ دەنگی ئاگاداری ڕێگەپێبدات.", "Tarayıcının alarm sesine izin vermesi için sayfada herhangi bir yere bir kez tıklayın."],
    ["Fault", "عطل", "هەڵە", "Arıza"], ["new alarm", "إنذار جديد", "ئاگاداری نوێ", "yeni alarm"], 
    ["grid not available", "الشبكة غير متاحة", "گرید بەردەست نییە", "şebeke yok"],
    ["Quick ranges", "فترات سريعة", "ماوە خێراکان", "Hızlı aralıklar"], ["Custom range", "فترة مخصصة", "ماوەی تایبەت", "Özel aralık"], ["Apply", "تطبيق", "جێبەجێکردن", "Uygula"],
    ["Last 24 hours", "آخر 24 ساعة", "دوایین ٢٤ کاتژمێر", "Son 24 saat"], ["This week", "هذا الأسبوع", "ئەم هەفتەیە", "Bu hafta"], ["Last month", "الشهر الماضي", "مانگی ڕابردوو", "Geçen ay"],
    ["Export this range (.xlsx)", "تصدير هذه الفترة (.xlsx)", "هەناردنی ئەم ماوەیە (.xlsx)", "Bu aralığı dışa aktar (.xlsx)"], ["Peaks in this period", "القمم في هذه الفترة", "لووتکەکان لەم ماوەیەدا", "Bu dönemdeki tepeler"],
    ["Full screen", "ملء الشاشة", "پڕ بە شاشە", "Tam ekran"], ["Showing", "يُعرض", "پیشاندان", "Gösterilen"],
    ["Hour by hour", "ساعة بساعة", "کاتژمێر بە کاتژمێر", "Saat saat"], ["Timeline of the day", "الجدول الزمني لليوم", "هێڵی کاتی ڕۆژ", "Günün zaman çizelgesi"], ["Peaks of the day", "قمم اليوم", "لووتکەکانی ڕۆژ", "Günün tepeleri"],
    ["Highest site load", "أعلى حمل للموقع", "بەرزترین لۆدی شوێن", "En yüksek tesis yükü"], ["Highest solar", "أعلى طاقة شمسية", "بەرزترین خۆر", "En yüksek güneş"], ["Highest gensets", "أعلى قدرة للمولدات", "بەرزترین مۆلیدەکان", "En yüksek jeneratör"],
    ["Highest grid import", "أعلى استيراد من الشبكة", "بەرزترین هاوردەی گرید", "En yüksek şebeke alımı"], ["Lowest voltage", "أدنى جهد", "نزمترین ڤۆڵت", "En düşük gerilim"], ["At", "عند", "لە", "Saat"],
    ["Solar producing", "الطاقة الشمسية تنتج", "خۆر بەرهەم دەهێنێت", "Güneş üretiyor"], ["SAU link", "اتصال SAU", "پەیوەندی SAU", "SAU bağlantısı"], ["NEO alarm", "إنذار NEO", "ئاگاداری NEO", "NEO alarmı"],
    ["Open the day report", "افتح التقرير اليومي", "ڕاپۆرتی ڕۆژانە بکەرەوە", "Günlük raporu aç"], ["The whole day", "اليوم كاملاً", "هەموو ڕۆژەکە", "Tüm gün"],
    ["Daily operations report", "تقرير التشغيل اليومي", "ڕاپۆرتی ڕۆژانەی کارکردن", "Günlük işletme raporu"], ["Summary", "الملخص", "پوختە", "Özet"], ["Energy", "الطاقة", "وزە", "Enerji"],
    ["Grid and gensets", "الشبكة والمولدات", "گرید و مۆلیدەکان", "Şebeke ve jeneratörler"], ["Incidents", "الحوادث", "ڕووداوەکان", "Olaylar"], ["Device log", "سجل الأجهزة", "تۆماری ئامێرەکان", "Cihaz günlüğü"],
    ["Remarks and sign-off", "الملاحظات والتوقيع", "تێبینی و واژۆ", "Notlar ve onay"], ["Remarks / actions", "ملاحظات / إجراءات", "تێبینی / کردار", "Notlar / yapılacaklar"],
    ["Checked by — shift supervisor", "تمت المراجعة — مشرف الوردية", "پشکنراوە لەلایەن — سەرپەرشتیاری شیفت", "Kontrol eden — vardiya amiri"], ["Approved by — plant manager", "تمت الموافقة — مدير المحطة", "پەسەندکراوە لەلایەن — بەڕێوەبەری وێستگە", "Onaylayan — santral müdürü"],
    ["Name", "الاسم", "ناو", "Ad"], ["Signature", "التوقيع", "واژۆ", "İmza"], ["Date", "التاريخ", "بەروار", "Tarih"], ["Period", "الفترة", "ماوە", "Dönem"], ["Report no.", "رقم التقرير", "ژمارەی ڕاپۆرت", "Rapor no."],
    ["Generated", "أُنشئ", "دروستکراوە", "Oluşturuldu"], ["Quantity", "الكمية", "بڕ", "Büyüklük"], ["Time in band", "الوقت ضمن النطاق", "کات لەناو سنوور", "Bant içinde süre"], ["Source", "المصدر", "سەرچاوە", "Kaynak"],
    ["Total", "الإجمالي", "کۆ", "Toplam"], ["Answered", "الاستجابة", "وەڵامدانەوە", "Yanıt"], ["Log", "السجل", "تۆمار", "Kayıt"], ["normal", "طبيعي", "ئاسایی", "normal"], ["Page", "صفحة", "پەڕە", "Sayfa"], ["of", "من", "لە", "/"],
    ["not answering — no data", "لا يستجيب — لا بيانات", "وەڵام ناداتەوە — داتا نییە", "yanıt vermiyor — veri yok"], ["controller alarm active", "إنذار المتحكم نشط", "ئاگاداری کۆنترۆڵەر چالاکە", "kontrolör alarmı aktif"],
    ["✓ Worked normally all day:", "✓ عملت بشكل طبيعي طوال اليوم:", "✓ بە ئاسایی کاریان کرد هەموو ڕۆژ:", "✓ Gün boyu normal çalıştı:"], ["InteliNeo master controller", "المتحكم الرئيسي InteliNeo", "کۆنترۆڵەری سەرەکی InteliNeo", "InteliNeo ana kontrolör"],
    ["SAU logger · Sineng gateway", "مسجّل SAU · بوابة Sineng", "تۆمارکەری SAU · دەروازەی Sineng", "SAU kaydedici · Sineng ağ geçidi"], ["InteliMains 210 incomer", "مغذي InteliMains 210", "هێڵی InteliMains 210", "InteliMains 210 girişi"],
    // day report
    ["Daily plant report", "التقرير اليومي للمحطة", "ڕاپۆرتی ڕۆژانەی وێستگە", "Günlük santral raporu"], ["The day in numbers", "اليوم بالأرقام", "ڕۆژەکە بە ژمارە", "Rakamlarla gün"],
    ["Power through the day", "الطاقة خلال اليوم", "وزە بە درێژایی ڕۆژ", "Gün boyunca güç"], ["Energy by source", "الطاقة حسب المصدر", "وزە بەپێی سەرچاوە", "Kaynağa göre enerji"], ["Inverters", "العاكسات", "ئینڤێرتەرەکان", "İnvertörler"],
    ["Alarms and warnings", "الإنذارات والتحذيرات", "ئاگاداری و هۆشدارییەکان", "Alarmlar ve uyarılar"], ["Print / Save as PDF", "طباعة / حفظ كـ PDF", "چاپ / پاشەکەوت وەک PDF", "Yazdır / PDF olarak kaydet"], ["Today", "اليوم", "ئەمڕۆ", "Bugün"],
    ["← Dashboard", "← لوحة المراقبة", "← داشبۆرد", "← Pano"], ["Gensets", "المولدات", "مۆلیدەکان", "Jeneratörler"], ["Outage", "انقطاع", "پچڕان", "Kesinti"], ["Duration", "المدة", "ماوە", "Süre"],
    ["Genset", "المولد", "مۆلیدە", "Jeneratör"], ["Run time", "مدة التشغيل", "ماوەی کارکردن", "Çalışma süresi"], ["Hours", "الساعات", "کاتژمێر", "Saat"], ["Starts", "مرات التشغيل", "دەستپێکردن", "Çalıştırma"], ["Energy", "الطاقة", "وزە", "Enerji"],
  ];
  // composed texts: [regex on the English text, (match) => [ar, ckb, tr]]
  const MI = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11 };
  const MONTHS = {
    ar: ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران", "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول"],
    ckb: ["کانوونی دووەم", "شوبات", "ئازار", "نیسان", "ئایار", "حوزەیران", "تەممووز", "ئاب", "ئەیلوول", "تشرینی یەکەم", "تشرینی دووەم", "کانوونی یەکەم"],
    tr: ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"],
  };
  // Kurdish (Sorani) day names, Sunday first: the browsers do not all know them
  const KU_DAYS = ["یەکشەممە", "دووشەممە", "سێشەممە", "چوارشەممە", "پێنجشەممە", "هەینی", "شەممە"];
  const UNITS = { s: ["ث", "چرکە", "sn"], min: ["د", "خولەک", "dk"], h: ["س", "کاتژمێر", "sa"] };
  const RX = [
    // live reading (the master every few seconds) and how often each device is read
    [/^read live, every ([\d.,]+) (s|min|h)$/, (m) => { const u = UNITS[m[2]]; return [`يُقرأ مباشرة، كل ${m[1]} ${u[0]}`, `ڕاستەوخۆ دەخوێندرێتەوە، هەر ${m[1]} ${u[1]}`, `canlı okunuyor, her ${m[1]} ${u[2]}`]; }],
    [/^live, every ([\d.,]+) (s|min|h)$/, (m) => { const u = UNITS[m[2]]; return [`مباشر، كل ${m[1]} ${u[0]}`, `ڕاستەوخۆ، هەر ${m[1]} ${u[1]}`, `canlı, her ${m[1]} ${u[2]}`]; }],
    [/^read every ([\d.,]+) (s|min|h)$/, (m) => { const u = UNITS[m[2]]; return [`يُقرأ كل ${m[1]} ${u[0]}`, `هەر ${m[1]} ${u[1]} دەخوێندرێتەوە`, `her ${m[1]} ${u[2]} okunuyor`]; }],
    [/^every ([\d.,]+) (s|min|h)$/, (m) => { const u = UNITS[m[2]]; return [`كل ${m[1]} ${u[0]}`, `هەر ${m[1]} ${u[1]}`, `her ${m[1]} ${u[2]}`]; }],
    [/^(inverters|incomers) read (live, every|every) ([\d.,]+) (s|min|h)$/, (m) => { const u = UNITS[m[4]], w = m[1] === "inverters" ? ["العواكس", "ئینڤێرتەرەکان", "eviriciler"] : ["المغذيات", "هێڵە هاتووەکان", "girişler"];
      return [`${w[0]} تُقرأ كل ${m[3]} ${u[0]}`, `${w[1]} هەر ${m[3]} ${u[1]} دەخوێندرێنەوە`, `${w[2]} her ${m[3]} ${u[2]} okunuyor`]; }],
    [/^master every ([\d.,]+) (s|min|h) · site read (\d\d:\d\d)$/, (m) => { const u = UNITS[m[2]]; return [`المتحكم الرئيسي كل ${m[1]} ${u[0]} · قراءة الموقع ${m[3]}`, `کۆنترۆڵەری سەرەکی هەر ${m[1]} ${u[1]} · خوێندنەوەی وێستگە ${m[3]}`, `ana kontrolör her ${m[1]} ${u[2]} · tesis okuması ${m[3]}`]; }],
    [/^live · master every ([\d.,]+) (s|min|h) · site read (\d\d:\d\d:\d\d), every ([\d.,]+) (s|min|h)$/, (m) => { const v = UNITS[m[2]], u = UNITS[m[5]];
      return [`مباشر · المتحكم الرئيسي كل ${m[1]} ${v[0]} · قراءة الموقع ${m[3]}، كل ${m[4]} ${u[0]}`, `ڕاستەوخۆ · کۆنترۆڵەری سەرەکی هەر ${m[1]} ${v[1]} · خوێندنەوەی وێستگە ${m[3]}، هەر ${m[4]} ${u[1]}`, `canlı · ana kontrolör her ${m[1]} ${v[2]} · tesis okuması ${m[3]}, her ${m[4]} ${u[2]}`]; }],
    [/^master every ([\d.,]+) (s|min|h) · (\d\d:\d\d:\d\d)$/, (m) => { const u = UNITS[m[2]]; return [`المتحكم الرئيسي كل ${m[1]} ${u[0]} · ${m[3]}`, `کۆنترۆڵەری سەرەکی هەر ${m[1]} ${u[1]} · ${m[3]}`, `ana kontrolör her ${m[1]} ${u[2]} · ${m[3]}`]; }],
    [/^master slow or not answering · last good answer (.+)$/, (m) => [`المتحكم الرئيسي بطيء أو لا يستجيب · آخر استجابة سليمة ${m[1]}`, `کۆنترۆڵەری سەرەکی خاوە یان وەڵام ناداتەوە · دوایین وەڵامی باش ${m[1]}`, `ana kontrolör yavaş veya yanıt vermiyor · son iyi yanıt ${m[1]}`]],
    [/^master every ([\d.,]+) (s|min|h)$/, (m) => { const u = UNITS[m[2]]; return [`المتحكم الرئيسي كل ${m[1]} ${u[0]}`, `کۆنترۆڵەری سەرەکی هەر ${m[1]} ${u[1]}`, `ana kontrolör her ${m[1]} ${u[2]}`]; }],
    [/^with every site read \(([\d.,]+) (s|min|h)\)$/, (m) => { const u = UNITS[m[2]]; return [`مع كل قراءة للموقع (${m[1]} ${u[0]})`, `لەگەڵ هەر خوێندنەوەیەکی وێستگە (${m[1]} ${u[1]})`, `her tesis okumasında (${m[1]} ${u[2]})`]; }],
    [/^(.+) · the PV array and (\d+) inverters \((\d+) devices\)$/, (m) => [`${m[1]} · مصفوفة الطاقة الشمسية و${m[2]} عواكس (${m[3]} أجهزة)`, `${m[1]} · ڕیزی پانێڵەکان و ${m[2]} ئینڤێرتەر (${m[3]} ئامێر)`, `${m[1]} · PV dizisi ve ${m[2]} evirici (${m[3]} cihaz)`]],
    [/^(.+) · who powers the plant, gensets, mains, solar$/, (m) => [`${m[1]} · من يغذي المحطة، المولدات، الشبكة، الطاقة الشمسية`, `${m[1]} · کێ وێستگە دەداتەوە، مۆلیدەکان، گرید، خۆر`, `${m[1]} · santrali kim besliyor, jeneratörler, şebeke, güneş`]],
    [/^from the site read · every ([\d.,]+) (s|min|h)$/, (m) => { const u = UNITS[m[2]]; return [`من قراءة الموقع · كل ${m[1]} ${u[0]}`, `لە خوێندنەوەی وێستگەوە · هەر ${m[1]} ${u[1]}`, `tesis okumasından · her ${m[1]} ${u[2]}`]; }],
    [/^COMM FAIL since (\d\d:\d\d:\d\d)$/, (m) => [`انقطاع الاتصال منذ ${m[1]}`, `پەیوەندی پچڕاوە لە ${m[1]}ەوە`, `${m[1]} itibarıyla iletişim hatası`]],
    [/^(\d\d:\d\d) · ([\d.,]+) (s|min|h) ago$/, (m) => { const u = UNITS[m[3]]; return [`${m[1]} · قبل ${m[2]} ${u[0]}`, `${m[1]} · ${m[2]} ${u[1]} لەمەوبەر`, `${m[1]} · ${m[2]} ${u[2]} önce`]; }],
    [/^\((\d+) h (\d+) min\)$/, (m) => [`(${m[1]} س ${m[2]} د)`, `(${m[1]} کاتژمێر ${m[2]} خولەک)`, `(${m[1]} sa ${m[2]} dk)`]],
    [/^\((\d+) min\)$/, (m) => [`(${m[1]} دقيقة)`, `(${m[1]} خولەک)`, `(${m[1]} dk)`]],
    [/^(\d+) of (\d+)$/, (m) => [`${m[1]} من ${m[2]}`, `${m[1]} لە ${m[2]}`, `${m[2]} içinde ${m[1]}`]],
    [/^of (\d+)$/, (m) => [`من ${m[1]}`, `لە ${m[1]}`, `/ ${m[1]}`]],
    [/^(\d+) devices$/, (m) => [`${m[1]} أجهزة`, `${m[1]} ئامێر`, `${m[1]} cihaz`]],
    [/^([\d,.]+) of ([\d,.]+) kWh$/, (m) => [`${m[1]} من ${m[2]} kWh`, `${m[1]} لە ${m[2]} kWh`, `${m[2]} kWh içinde ${m[1]}`]],
    [/^last read (\d\d:\d\d:\d\d) · (\d+) s ago$/, (m) => [`آخر قراءة ${m[1]} · قبل ${m[2]} ث`, `دوایین خوێندنەوە ${m[1]} · ${m[2]} چرکە لەمەوبەر`, `son okuma ${m[1]} · ${m[2]} sn önce`]],
    [/^last read (\d\d:\d\d:\d\d) · (\d+) min ago$/, (m) => [`آخر قراءة ${m[1]} · قبل ${m[2]} د`, `دوایین خوێندنەوە ${m[1]} · ${m[2]} خولەک لەمەوبەر`, `son okuma ${m[1]} · ${m[2]} dk önce`]],
    [/^last read (\d\d:\d\d:\d\d)$/, (m) => [`آخر قراءة ${m[1]}`, `دوایین خوێندنەوە ${m[1]}`, `son okuma ${m[1]}`]],
    [/^(\d+) incomers? · (\d+) gensets · (solar feeder|no solar) · bus (live|DEAD)$/, (m) => [`${m[1]} مغذيات · ${m[2]} مولدات · ${m[3] === 'no solar' ? 'بدون طاقة شمسية' : 'مغذي شمسي'} · القضيب ${m[4] === 'live' ? 'مكهرب' : 'بلا جهد'}`, `${m[1]} هێڵی هاتوو · ${m[2]} مۆلیدە · ${m[3] === 'no solar' ? 'بێ خۆر' : 'هێڵی خۆر'} · باس ${m[4] === 'live' ? 'کارەبای تێدایە' : 'بێ کارەبایە'}`, `${m[1]} giriş · ${m[2]} jeneratör · ${m[3] === 'no solar' ? 'güneş yok' : 'güneş fideri'} · bara ${m[4] === 'live' ? 'enerjili' : 'enerjisiz'}`]],
    [/^(\d+)\/(\d+) inverters$/, (m) => [`${m[1]}/${m[2]} عواكس`, `${m[1]}/${m[2]} ئینڤێرتەر`, `${m[1]}/${m[2]} evirici`]],
    [/^Gensets (.+) kW · (.+) kVAr · (\d+) of (\d+) on line$/, (m) => [`المولدات ${m[1]} kW · ${m[2]} kVAr · ${m[3]} من ${m[4]} في الخدمة`, `مۆلیدەکان ${m[1]} kW · ${m[2]} kVAr · ${m[3]} لە ${m[4]} کار دەکەن`, `Jeneratörler ${m[1]} kW · ${m[2]} kVAr · ${m[4]} içinde ${m[3]} devrede`]],
    [/^fuel not metered · ≈(.+) L at (.+) L\/kWh$/, (m) => [`الوقود غير مقاس · ≈${m[1]} لتر بمعدل ${m[2]} L/kWh`, `سووتەمەنی نەپێوراوە · ≈${m[1]} لیتر بە ${m[2]} L/kWh`, `yakıt ölçülmüyor · ≈${m[1]} L, ${m[2]} L/kWh ile`]],
    [/^([\d,.]+) kWh solar curtailed today$/, (m) => [`${m[1]} kWh طاقة شمسية مقلصة اليوم`, `${m[1]} kWh خۆر کەمکرایەوە ئەمڕۆ`, `bugün ${m[1]} kWh güneş kısıldı`]],
    [/^supply ([\d.,]+) h today · ([\d.,]+) % of the energy$/, (m) => [`إمداد ${m[1]} س اليوم · ${m[2]} % من الطاقة`, `دابینکردن ${m[1]} کاتژمێر ئەمڕۆ · ${m[2]} % ی وزە`, `bugün ${m[1]} sa besleme · enerjinin %${m[2]}'i`]],
    [/^(\d+) of (\d+) running · (.+) kW$/, (m) => [`${m[1]} من ${m[2]} تعمل · ${m[3]} kW`, `${m[1]} لە ${m[2]} کار دەکەن · ${m[3]} kW`, `${m[2]} içinde ${m[1]} çalışıyor · ${m[3]} kW`]],
    [/^(.+) · ([\d.,]+) genset-hours in total$/, (m) => [`${trl(m[1], 'ar')} · ${m[2]} ساعة تشغيل مولدات إجمالاً`, `${trl(m[1], 'ckb')} · ${m[2]} کاتژمێری مۆلیدە بە گشتی`, `${trl(m[1], 'tr')} · toplam ${m[2]} jeneratör-saat`]],
    [/^· (\d+) starts?$/, (m) => [`· ${m[1]} تشغيل`, `· ${m[1]} دەستپێکردن`, `· ${m[1]} başlatma`]],
    [/^([\d,]+) starts in total$/, (m) => [`${m[1]} تشغيل إجمالاً`, `${m[1]} دەستپێکردن بە گشتی`, `toplam ${m[1]} başlatma`]],
    [/^(.+) kW now · (.+) kWh today · (\d+) inverters$/, (m) => [`${m[1]} kW الآن · ${m[2]} kWh اليوم · ${m[3]} عواكس`, `${m[1]} kW ئێستا · ${m[2]} kWh ئەمڕۆ · ${m[3]} ئینڤێرتەر`, `şu an ${m[1]} kW · bugün ${m[2]} kWh · ${m[3]} evirici`]],
    [/^(.+) kW now · (.+) kWh today$/, (m) => [`${m[1]} kW الآن · ${m[2]} kWh اليوم`, `${m[1]} kW ئێستا · ${m[2]} kWh ئەمڕۆ`, `şu an ${m[1]} kW · bugün ${m[2]} kWh`]],
    [/^(.+) kW held back$/, (m) => [`${m[1]} kW محجوبة`, `${m[1]} kW ڕاگیراو`, `${m[1]} kW tutuldu`]],
    [/^([\d,.]+) kWh curtailed$/, (m) => [`${m[1]} kWh مقلصة`, `${m[1]} kWh کەمکرایەوە`, `${m[1]} kWh kısıldı`]],
    [/^rated (.+) kW each$/, (m) => [`${m[1]} kW اسمية لكل منها`, `هەر یەکەیان ${m[1]} kW`, `her biri ${m[1]} kW`]],
    [/^fleet average (.+) kWh · (.+) kWh\/kWp today \((.+) kWp\)$/, (m) => [`المتوسط ${m[1]} kWh · ${m[2]} kWh/kWp اليوم (${m[3]} kWp)`, `تێکڕا ${m[1]} kWh · ${m[2]} kWh/kWp ئەمڕۆ (${m[3]} kWp)`, `filo ortalaması ${m[1]} kWh · bugün ${m[2]} kWh/kWp (${m[3]} kWp)`]],
    [/^grid on · (.+) kW$/, (m) => [`الشبكة متاحة · ${m[1]} kW`, `گرید هەیە · ${m[1]} kW`, `şebeke var · ${m[1]} kW`]],
    [/^In band (.+)$/, (m) => [`ضمن النطاق ${m[1]}`, `لە سنووردا ${m[1]}`, `Bant içi ${m[1]}`]],
    [/^(\d+) errors?$/, (m) => [`${m[1]} أخطاء`, `${m[1]} هەڵە`, `${m[1]} hata`]],
    [/^(\d+) info$/, (m) => [`${m[1]} معلومات`, `${m[1]} زانیاری`, `${m[1]} bilgi`]],
    [/^(\d+) warnings? · (\d+) info$/, (m) => [`${m[1]} تحذيرات · ${m[2]} معلومات`, `${m[1]} هۆشداری · ${m[2]} زانیاری`, `${m[1]} uyarı · ${m[2]} bilgi`]],
    [/^([\d,]+) rows · ([\d,]+) columns · about (.+)$/, (m) => [`${m[1]} صفاً · ${m[2]} عموداً · حوالي ${m[3]}`, `${m[1]} ڕیز · ${m[2]} ستوون · نزیکەی ${m[3]}`, `${m[1]} satır · ${m[2]} sütun · yaklaşık ${m[3]}`]],
    [/^(\d+) min$/, (m) => [`${m[1]} دقيقة`, `${m[1]} خولەک`, `${m[1]} dk`]],
    [/^(\d+) hours?$/, (m) => [`${m[1]} ساعة`, `${m[1]} کاتژمێر`, `${m[1]} saat`]],
    [/^(\d+) days?$/, (m) => [`${m[1]} يوم`, `${m[1]} ڕۆژ`, `${m[1]} gün`]],
    [/^(\d+) s$/, (m) => [`${m[1]} ث`, `${m[1]} چرکە`, `${m[1]} sn`]],
    [/^this computer's time \((.+)\)$/, (m) => [`توقيت هذا الحاسوب (${m[1]})`, `کاتی ئەم کۆمپیوتەرە (${m[1]})`, `bu bilgisayarın saati (${m[1]})`]],
    [/^(.+) \(from config\)$/, (m) => [`${trl(m[1], 'ar')} (من الإعدادات)`, `${trl(m[1], 'ckb')} (لە ڕێکخستنەوە)`, `${trl(m[1], 'tr')} (yapılandırmadan)`]],
    [/^demo: refreshes every (.+) regardless$/, (m) => [`تجريبي: يتحدث كل ${trl(m[1], 'ar')} دائماً`, `دیمۆ: هەر ${trl(m[1], 'ckb')} نوێ دەبێتەوە`, `demo: her durumda ${trl(m[1], 'tr')}'de bir yenilenir`]],
    [/^on · (.+) · (\d+) tags · (\d+) reads$/, (m) => [`يعمل · ${m[1]} · ${m[2]} وسماً · ${m[3]} قراءة`, `کار دەکات · ${m[1]} · ${m[2]} تاگ · ${m[3]} خوێندنەوە`, `açık · ${m[1]} · ${m[2]} etiket · ${m[3]} okuma`]],
    [/^Version (.+) · All rights reserved$/, (m) => [`الإصدار ${m[1]} · جميع الحقوق محفوظة`, `وەشانی ${m[1]} · هەموو مافەکان پارێزراون`, `Sürüm ${m[1]} · Tüm hakları saklıdır`]],
    [/^Show or hide (.+)$/, (m) => [`إظهار أو إخفاء ${trl(m[1], 'ar')}`, `پیشاندان یان شاردنەوەی ${trl(m[1], 'ckb')}`, `${trl(m[1], 'tr')} göster / gizle`]],
    [/^Trend of (.+)$/, (m) => [`منحنى ${m[1]}`, `گرافی ${m[1]}`, `${m[1]} eğilimi`]],
    [/^(\d{4}-\d\d-\d\d \d\d:\d\d) · no data · click for details?$/, (m) => [`${m[1]} · لا بيانات · انقر للتفاصيل`, `${m[1]} · داتا نییە · کرتە بکە بۆ وردەکاری`, `${m[1]} · veri yok · ayrıntı için tıklayın`]],
    [/^(\d{4}-\d\d-\d\d \d\d:\d\d) · (.+) kW · click for details?$/, (m) => [`${m[1]} · ${m[2]} kW · انقر للتفاصيل`, `${m[1]} · ${m[2]} kW · کرتە بکە بۆ وردەکاری`, `${m[1]} · ${m[2]} kW · ayrıntı için tıklayın`]],
    [/^Open the day report of (.+)$/, (m) => [`فتح التقرير اليومي لـ ${m[1]}`, `کردنەوەی ڕاپۆرتی ڕۆژی ${m[1]}`, `${m[1]} günlük raporunu aç`]],
    [/^(.+): all values$/, (m) => [`${m[1]}: كل القيم`, `${m[1]}: هەموو بەهاکان`, `${m[1]}: tüm değerler`]],
    [/^Gen (\d+)$/, (m) => [`مولد ${m[1]}`, `مۆلیدەی ${m[1]}`, `Jen. ${m[1]}`]],
    [/^Gen (\d+) stopped$/, (m) => [`توقف المولد ${m[1]}`, `مۆلیدەی ${m[1]} وەستا`, `Jeneratör ${m[1]} durdu`]],
    [/^Gen (\d+) started, GCB closed$/, (m) => [`بدأ المولد ${m[1]}، أُغلق GCB`, `مۆلیدەی ${m[1]} دەستی پێکرد، GCB داخرا`, `Jeneratör ${m[1]} başladı, GCB kapandı`]],
    [/^(\d+) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)( \d\d:\d\d(?::\d\d)?)?$/, (m) => [`${m[1]} ${MONTHS.ar[MI[m[2]]]}${m[3] || ''}`, `${m[1]}ی ${MONTHS.ckb[MI[m[2]]]}${m[3] || ''}`, `${m[1]} ${MONTHS.tr[MI[m[2]]]}${m[3] || ''}`]],
    [/^day in progress · up to (\d\d:\d\d) · generated (.+)$/, (m) => [`اليوم جارٍ · حتى ${m[1]} · أُنشئ ${m[2]}`, `ڕۆژەکە بەردەوامە · تا ${m[1]} · دروستکرا ${m[2]}`, `gün sürüyor · ${m[1]}'e kadar · oluşturuldu ${m[2]}`]],
    [/^generated (.+)$/, (m) => [`أُنشئ ${m[1]}`, `دروستکرا ${m[1]}`, `oluşturuldu ${m[1]}`]],
    [/^(\d+) outages?$/, (m) => [`${m[1]} انقطاع`, `${m[1]} پچڕان`, `${m[1]} kesinti`]],
    [/^([\d.,]+) genset-hours$/, (m) => [`${m[1]} ساعة تشغيل مولدات`, `${m[1]} کاتژمێری مۆلیدە`, `${m[1]} jeneratör-saat`]],
    [/^(\d+) faults?$/, (m) => [`${m[1]} أعطال`, `${m[1]} هەڵە`, `${m[1]} arıza`]],
    [/^(\d+) faults? · (\d+) warnings?$/, (m) => [`${m[1]} أعطال · ${m[2]} تحذيرات`, `${m[1]} هەڵە · ${m[2]} هۆشداری`, `${m[1]} arıza · ${m[2]} uyarı`]],
    [/^peak (.+) kW at (\d\d:\d\d)$/, (m) => [`الذروة ${m[1]} kW عند ${m[2]}`, `لووتکە ${m[1]} kW لە ${m[2]}`, `zirve ${m[1]} kW, ${m[2]}`]],
    [/^≈ (.+) L of diesel$/, (m) => [`≈ ${m[1]} لتر ديزل`, `≈ ${m[1]} لیتر دیزڵ`, `≈ ${m[1]} L dizel`]],
    [/^available ([\d.,]+) h$/, (m) => [`متاحة ${m[1]} س`, `بەردەست ${m[1]} کاتژمێر`, `${m[1]} sa mevcut`]],
    [/^at (.+) L per kWh of solar$/, (m) => [`بمعدل ${m[1]} لتر لكل kWh شمسية`, `بە ${m[1]} لیتر بۆ هەر kWh خۆر`, `güneşin kWh'ı başına ${m[1]} L ile`]],
    [/^at (.+) per litre$/, (m) => [`بسعر ${m[1]} للتر`, `بە ${m[1]} بۆ هەر لیترێک`, `litresi ${m[1]}`]],
    [/^(.+) kg per litre$/, (m) => [`${m[1]} كغ لكل لتر`, `${m[1]} کگ بۆ هەر لیترێک`, `litre başına ${m[1]} kg`]],
    [/^Grid available (.+) h · off (.+) h$/, (m) => [`الشبكة متاحة ${m[1]} س · مقطوعة ${m[2]} س`, `گرید بەردەست ${m[1]} کاتژمێر · نەبوو ${m[2]} کاتژمێر`, `şebeke ${m[1]} sa var · ${m[2]} sa yok`]],
    [/^([\d.,]+) genset-hours · (\d+) starts? · ≈ (.+) L of diesel$/, (m) => [`${m[1]} ساعة تشغيل · ${m[2]} تشغيل · ≈ ${m[3]} لتر ديزل`, `${m[1]} کاتژمێری مۆلیدە · ${m[2]} دەستپێکردن · ≈ ${m[3]} لیتر دیزڵ`, `${m[1]} jeneratör-saat · ${m[2]} başlatma · ≈ ${m[3]} L dizel`]],
    [/^Time within (.+)$/, (m) => [`الوقت ضمن ${m[1]}`, `کات لە نێوان ${m[1]}`, `${m[1]} içinde geçen süre`]],
    [/^(\d+) devices · (\d+) gensets$/, (m) => [`${m[1]} أجهزة · ${m[2]} مولدات`, `${m[1]} ئامێر · ${m[2]} مۆلیدە`, `${m[1]} cihaz · ${m[2]} jeneratör`]],
    [/^SAU gateway \((.+)\) \+ (\d+) inverters behind it · (.+)$/, (m) => [`بوابة SAU (${m[1]}) + ${m[2]} عواكس خلفها · ${m[3]}`, `دەروازەی SAU (${m[1]}) + ${m[2]} ئینڤێرتەری پشتی · ${m[3]}`, `SAU ağ geçidi (${m[1]}) + arkasındaki ${m[2]} evirici · ${m[3]}`]],
    [/^(.+) · all (\d+) inverters$/, (m) => [`${m[1]} · كل العواكس الـ ${m[2]}`, `${m[1]} · هەموو ${m[2]} ئینڤێرتەرەکە`, `${m[1]} · ${m[2]} eviricinin hepsi`]],
    [/^Diesel figures are estimates \((.+) L per kWh, (.+) kg CO₂ per litre\)\. Times are plant time\. Energy from 15-minute averages of the readings\.$/, (m) => [`أرقام الديزل تقديرية (${m[1]} لتر لكل kWh، ${m[2]} كغ CO₂ لكل لتر). الأوقات بتوقيت المحطة. الطاقة من متوسطات القراءات كل 15 دقيقة.`, `ژمارەکانی دیزڵ خەمڵاندنن (${m[1]} لیتر بۆ هەر kWh، ${m[2]} کگ CO₂ بۆ هەر لیترێک). کاتەکان کاتی وێستگەن. وزە لە تێکڕای 15 خولەکی خوێندنەوەکانەوەیە.`, `Dizel rakamları tahminidir (kWh başına ${m[1]} L, litre başına ${m[2]} kg CO₂). Saatler santral saatidir. Enerji okumaların 15 dakikalık ortalamalarındandır.`]],
    [/^(.+) · export every (.+), about ([\d,]+) rows$/, (m) => [`${trl(m[1], "ar")} · تصدير كل ${trl(m[2], "ar")}، حوالي ${m[3]} صفاً`, `${trl(m[1], "ckb")} · هەناردن هەر ${trl(m[2], "ckb")}، نزیکەی ${m[3]} ڕیز`, `${trl(m[1], "tr")} · ${trl(m[2], "tr")}'da bir dışa aktarım, yaklaşık ${m[3]} satır`]],
    [/^(.+) link$/, (m) => [`اتصال ${m[1]}`, `پەیوەندی ${m[1]}`, `${m[1]} bağlantısı`]],
    [/^Sineng SAU-100 virtual array (.*)$/, (m) => [`مصفوفة Sineng SAU-100 الافتراضية ${m[1]}`, `کۆمەڵەی ڤێرچواڵی Sineng SAU-100 ${m[1]}`, `Sineng SAU-100 sanal dizi ${m[1]}`]],
    [/^([a-z]+-[a-z0-9-]+): (.+)$/, (m) => [`${m[1]}: ${trl(m[2], 'ar')}`, `${m[1]}: ${trl(m[2], 'ckb')}`, `${m[1]}: ${trl(m[2], 'tr')}`]],
    [/^updated (\d\d:\d\d)$/, (m) => [`تحديث ${m[1]}`, `نوێکراوە ${m[1]}`, `güncellendi ${m[1]}`]],
    [/^no update since (\d\d:\d\d)$/, (m) => [`لا تحديث منذ ${m[1]}`, `هیچ نوێکردنەوەیەک لە ${m[1]}ەوە`, `${m[1]} sonrası güncelleme yok`]],
    [/^Last (\d+) hours$/, (m) => [`آخر ${m[1]} ساعة`, `دوایین ${m[1]} کاتژمێر`, `Son ${m[1]} saat`]],
    [/^Last (\d+) days$/, (m) => [`آخر ${m[1]} أيام`, `دوایین ${m[1]} ڕۆژ`, `Son ${m[1]} gün`]],
    [/^last (\d+) hours$/, (m) => [`آخر ${m[1]} ساعة`, `دوایین ${m[1]} کاتژمێر`, `son ${m[1]} saat`]],
    [/^last (\d+) days$/, (m) => [`آخر ${m[1]} أيام`, `دوایین ${m[1]} ڕۆژ`, `son ${m[1]} gün`]],
    [/^(\d+) of (\d+) running$/, (m) => [`${m[1]} من ${m[2]} تعمل`, `${m[1]} لە ${m[2]} کار دەکەن`, `${m[2]} jeneratörden ${m[1]} çalışıyor`]],
    [/^(\d+) of (\d+) on line$/, (m) => [`${m[1]} من ${m[2]} في الخدمة`, `${m[1]} لە ${m[2]} کار دەکەن`, `${m[2]} jeneratörden ${m[1]} devrede`]],
    [/^all (\d+) devices answering$/, (m) => [`جميع الأجهزة الـ ${m[1]} تستجيب`, `هەموو ${m[1]} ئامێرەکە وەڵام دەدەنەوە`, `${m[1]} cihazın tümü yanıt veriyor`]],
    [/^(\d+) devices? not answering$/, (m) => [`${m[1]} أجهزة لا تستجيب`, `${m[1]} ئامێر وەڵام نادەنەوە`, `${m[1]} cihaz yanıt vermiyor`]],
    [/^([\d,.]+) kWh today$/, (m) => [`${m[1]} kWh اليوم`, `${m[1]} kWh ئەمڕۆ`, `bugün ${m[1]} kWh`]],
    [/^(.+) \+ solar$/, (m) => [`${tr1(m[1], "ar")} + الطاقة الشمسية`, `${tr1(m[1], "ckb")} + خۆر`, `${tr1(m[1], "tr")} + güneş`]],
    [/^Mains incomers \((\d+)\)$/, (m) => [`مغذيات الشبكة (${m[1]})`, `هێڵەکانی تۆڕ (${m[1]})`, `Şebeke girişleri (${m[1]})`]],
    [/^Gensets on (\S+) \(breaker and CAN bus\)$/, (m) => [`المولدات على ${m[1]} (القاطع وناقل CAN)`, `مۆلیدەکان لەسەر ${m[1]} (پچڕێنەر و CAN)`, `${m[1]} üzerindeki jeneratörler (kesici ve CAN)`]],
    [/^Master controller · (\S+)$/, (m) => [`المتحكم الرئيسي · ${m[1]}`, `کۆنترۆڵەری سەرەکی · ${m[1]}`, `Ana kontrolör · ${m[1]}`]],
    [/^of ([\d,.]+) kW available$/, (m) => [`من ${m[1]} kW متاحة`, `لە ${m[1]} kW ی بەردەست`, `${m[1]} kW kullanılabilirden`]],
    [/^([\d,.]+) % from solar now$/, (m) => [`${m[1]} % من الطاقة الشمسية الآن`, `${m[1]} % لە خۆرەوە ئێستا`, `şu an %${m[1]} güneşten`]],
    [/^today · at ([\d,.]+) per litre$/, (m) => [`اليوم · بسعر ${m[1]} لكل لتر`, `ئەمڕۆ · بە ${m[1]} بۆ هەر لیترێک`, `bugün · litresi ${m[1]}`]],
    [/^2\.68 kg per litre of diesel$/, () => ["2.68 كغ لكل لتر ديزل", "2.68 کگ بۆ هەر لیترێک دیزڵ", "litre dizel başına 2,68 kg"]],
    [/^of ([\d,.]+) kWh$/, (m) => [`من ${m[1]} kWh`, `لە ${m[1]} kWh`, `${m[1]} kWh içinden`]],
    [/^(\d+) devices · (demo plant|read-only polling)$/, (m) => [`${m[1]} أجهزة · ${m[2] === "demo plant" ? "محطة تجريبية" : "قراءة فقط"}`, `${m[1]} ئامێر · ${m[2] === "demo plant" ? "وێستگەی تاقیکردنەوە" : "تەنها خوێندنەوە"}`, `${m[1]} cihaz · ${m[2] === "demo plant" ? "demo santral" : "salt okuma"}`]],
    [/^last read (\d\d:\d\d:\d\d) · every (.+)$/, (m) => [`آخر قراءة ${m[1]} · كل ${m[2]}`, `دوایین خوێندنەوە ${m[1]} · هەر ${m[2]}`, `son okuma ${m[1]} · her ${m[2]}`]],
    [/^(\d+) alarms?$/, (m) => [`${m[1]} ${m[1] === "1" ? "إنذار" : "إنذارات"}`, `${m[1]} ئاگاداری`, `${m[1]} alarm`]],
    [/^(\d+) warnings?$/, (m) => [`${m[1]} ${m[1] === "1" ? "تحذير" : "تحذيرات"}`, `${m[1]} هۆشداری`, `${m[1]} uyarı`]],
    [/^(\d+) alarms? to acknowledge$/, (m) => [`${m[1]} إنذار بحاجة إلى إقرار`, `${m[1]} ئاگاداری پێویستی بە پەسەندکردنە`, `onaylanacak ${m[1]} alarm`]],
  ];
  const IDX = { ar: 1, ckb: 2, tr: 3 };
  const dict = { ar: new Map(), ckb: new Map(), tr: new Map() };
  T.forEach((row) => { for (const l in IDX) dict[l].set(row[0], row[IDX[l]]); });
  function tr1(s, lang) { return dict[lang].get(s) || s; }
  // a part of a text in a given language: the fixed texts first, then the patterns (not the device-message one)
  function trl(s, l) {
    if (!s || !IDX[l]) return s;
    const hit = dict[l].get(s); if (hit) return hit;
    for (const [re, f] of RX) { if (re.source.startsWith("^([a-z]+-")) continue; const m = s.match(re); if (m) return f(m)[IDX[l] - 1]; }
    return s;
  }
  // dates in the chosen language; Kurdish by hand, because not every browser has the Kurdish calendar names
  const LOCS = { en: "en-GB", ar: "ar-IQ-u-nu-latn", ckb: "ckb-IQ-u-nu-latn", tr: "tr-TR" };
  const two = (n) => String(n).padStart(2, "0");
  function date(ms, kind) {
    const d = new Date(ms);
    if (lang === "ckb") {
      const day = d.getDate(), mon = MONTHS.ckb[d.getMonth()], y = d.getFullYear(), wd = KU_DAYS[d.getDay()];
      if (kind === "long") return `${wd}، ${day}ی ${mon} ${y}`;
      if (kind === "weekdayDay") return `${wd}، ${day}ی ${mon}`;
      if (kind === "month") return `${mon} ${y}`;
      if (kind === "narrow") return wd[0];
      if (kind === "dateTime") return `${day}ی ${mon} ${y}، ${two(d.getHours())}:${two(d.getMinutes())}`;
      return `${day}ی ${mon} ${y}`;
    }
    const o = { long: { weekday: "long", day: "numeric", month: "long", year: "numeric" }, weekdayDay: { weekday: "long", day: "numeric", month: "long" },
      month: { month: "long", year: "numeric" }, narrow: { weekday: "narrow" },
      dateTime: { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false } }[kind] || { day: "numeric", month: "short", year: "numeric" };
    return new Intl.DateTimeFormat(LOCS[lang] || "en-GB", o).format(d);
  }
  let lang = "en";
  try { lang = localStorage.getItem("pc.lang") || "en"; } catch (e) {}
  if (!LANGS[lang]) lang = "en";
  function t(s) {
    if (lang === "en" || typeof s !== "string") return s;
    const k = s.trim(); if (!k) return s;
    const hit = dict[lang].get(k);
    if (hit) return s.replace(k, hit);
    for (const [re, f] of RX) { const m = k.match(re); if (m) return s.replace(k, f(m)[IDX[lang] - 1]); }
    return s;
  }
  // translate what is on screen; the English original of every text node / attribute is kept for switching back
  const orig = new WeakMap(), ATTRS = ["placeholder", "title", "aria-label"];
  const skip = (n) => { const p = n.parentElement; return !p || p.closest("script, style, code, kbd, .mono, .num, input, textarea, [data-no-i18n]"); };
  function doNode(n) {
    if (n.nodeType === 3) {
      if (skip(n)) return;
      // rec.o = the English original, rec.w = what is on screen as last written here; anything else on screen
      // was written by the page itself (new English text)
      let rec = orig.get(n);
      if (!rec || n.nodeValue !== rec.w) { rec = { o: n.nodeValue, w: n.nodeValue }; orig.set(n, rec); }
      const want = lang === "en" ? rec.o : t(rec.o);
      if (n.nodeValue !== want) { rec.w = want; n.nodeValue = want; }
    } else if (n.nodeType === 1) {
      ATTRS.forEach((a) => {
        if (!n.hasAttribute(a)) return;
        const ko = "data-en-" + a, kw = "data-i18n-" + a, cur = n.getAttribute(a);
        if (!n.hasAttribute(ko) || cur !== n.getAttribute(kw)) { n.setAttribute(ko, cur); n.setAttribute(kw, cur); }
        const o = n.getAttribute(ko), want = lang === "en" ? o : t(o);
        if (cur !== want) { n.setAttribute(kw, want); n.setAttribute(a, want); }
      });
      if (n.matches && n.matches("script, style")) return;
      for (let c = n.firstChild; c; c = c.nextSibling) doNode(c);
    }
  }
  function apply(l) {
    if (l && LANGS[l]) { lang = l; try { localStorage.setItem("pc.lang", l); } catch (e) {} }
    document.documentElement.lang = lang; document.documentElement.dir = LANGS[lang].dir;
    doNode(document.body);
  }
  const obs = new MutationObserver((ms) => {
    if (lang === "en") return;            // English is the page's own text: nothing to do
    ms.forEach((m) => { if (m.type === "childList") m.addedNodes.forEach(doNode); else if (m.type === "characterData") doNode(m.target); else if (m.type === "attributes") doNode(m.target); });
  });
  function start() {
    apply();
    obs.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  window.CCI_I18N = { t, trl, date, apply, langs: LANGS, get lang() { return lang; } };
})();
