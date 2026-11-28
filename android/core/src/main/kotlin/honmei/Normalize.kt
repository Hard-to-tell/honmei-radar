package honmei

import java.text.Normalizer
import java.util.concurrent.ConcurrentHashMap

/** 文本规范化与同人本标题拆解（对应电脑版 normalize.py）。 */
object Norm {
    // 安卓的正则（ICU）默认就按 Unicode 处理 \w \d，而且不认 (?U)；电脑上的 JVM 要加 (?U)
    private val ANDROID = System.getProperty("java.vm.name")?.contains("Dalvik", true) == true
    private fun rx(p: String, ci: Boolean = false) = Regex((if (ANDROID) (if (ci) "(?i)" else "") else if (ci) "(?iU)" else "(?U)") + p)

    private val simpCache = ConcurrentHashMap<String, String>()
    fun nfkc(s: String): String = Normalizer.normalize(s, Normalizer.Form.NFKC)
    fun lower(s: String): String = s.lowercase()

    fun toSimplified(s: String?): String {
        val k = s ?: ""
        return simpCache.getOrPut(k) { OpenCC.convert(nfkc(k)) }
    }

    private val PUNCT = rx("[\\s\\-_~・·.,，。!！?？:：;；'\"“”‘’/\\\\|]+")

    fun norm(s: String?): String = PUNCT.replace(lower(toSimplified(s ?: "")), "")

    // ---------- 标签 ----------
    val META_TAGS = setOf(
        "禁漫书库", "禁漫汉化组", "中文", "汉化", "中国翻译", "中国翻訳", "中国语", "中国語", "日文", "日语", "日語", "英文", "english",
        "chinese", "japanese", "dl版", "重新上传", "重嵌", "扫图", "掃圖", "单行本", "單行本", "已完结", "完结",
        "连载中", "连载", "pixiv", "fanbox", "fantia", "patreon", "cg集", "cg", "无修正", "無修正",
    )

    val DEFAULT_ALIASES = linkedMapOf(
        "彩色" to "全彩", "full color" to "全彩", "fullcolor" to "全彩",
        "长篇" to "长篇", "長篇" to "长篇",
        "ntr" to "ntr", "寝取" to "ntr", "寝取り" to "ntr", "寝取られ" to "ntr", "绿帽" to "ntr",
        "纯爱" to "纯爱", "純愛" to "纯爱",
        "后宫" to "后宫", "後宮" to "后宫", "harem" to "后宫",
        "百合" to "百合", "yuri" to "百合", "gl" to "百合",
        "耽美" to "耽美", "bl" to "耽美", "yaoi" to "耽美",
        "巨乳" to "巨乳", "大乳" to "巨乳", "爆乳" to "巨乳",
        "贫乳" to "贫乳", "貧乳" to "贫乳", "小胸" to "贫乳",
        "萝莉" to "萝莉", "蘿莉" to "萝莉", "loli" to "萝莉",
        "人妻" to "人妻", "熟女" to "熟女",
        "触手" to "触手", "觸手" to "触手",
        "伪娘" to "伪娘", "偽娘" to "伪娘", "男の娘" to "伪娘", "扶他" to "扶他", "扶她" to "扶他", "futanari" to "扶他",
        "韩漫" to "韩漫", "韓漫" to "韩漫", "同人" to "同人", "单本" to "单本", "短篇" to "短篇",
        "3d" to "3d", "cosplay" to "cosplay",
        "透视" to "横切面", "断面图" to "横切面", "x光" to "横切面",
        "剧情向" to "剧情", "阿黑颜" to "阿嘿颜", "骑乗" to "骑乘", "骑乘位" to "骑乘",
        "过膝袜" to "长筒袜", "长筒丝袜" to "长筒袜", "连裤丝袜" to "连裤袜", "黑丝" to "丝袜",
        "女高中生(jk)" to "jk", "女高中生" to "jk", "妹妹系" to "妹妹", "姐姐系" to "姐姐",
        "后宫闪光" to "后宫", "手淫" to "自慰", "群交" to "3p", "多人" to "3p", "肛交" to "肛门",
        "乱伦" to "近亲", "近亲相交" to "近亲", "出轨" to "ntr",
        "近亲相交:妹" to "近亲", "近亲相交:姊" to "近亲", "近亲相交:母" to "近亲", "近亲相交:父" to "近亲",
        "近亲相奸" to "近亲", "近亲乱伦" to "近亲", "兄妹乱伦" to "近亲",
        "破处" to "处女", "情趣内衣" to "内衣", "野外 / 露出" to "露出", "野外露出" to "露出",
        "洗脑 / 催眠" to "催眠", "骑大车" to "骑乘", "兽耳娘" to "兽耳", "足の恋" to "足交",
        "扶他乐园" to "扶他", "伪娘哲学" to "伪娘", "百合花园" to "百合", "耽美花园" to "耽美", "重口地带" to "重口",
    )

    val PARODY_ALIASES = mapOf(
        "アイドルマスターシャイニーカラーズ" to "偶像大师 闪耀色彩", "シャイニーカラーズ" to "偶像大师 闪耀色彩",
        "闪耀色彩" to "偶像大师 闪耀色彩", "偶像大师闪耀色彩" to "偶像大师 闪耀色彩", "偶像大师" to "偶像大师",
        "アイドルマスター" to "偶像大师", "アイドルマスターシンデレラガールズ" to "偶像大师 灰姑娘女孩",
        "ブルーアーカイブ" to "蔚蓝档案", "蔚蓝档案" to "蔚蓝档案", "bluearchive" to "蔚蓝档案",
        "原神" to "原神", "genshinimpact" to "原神",
        "崩坏星穹铁道" to "崩坏：星穹铁道", "崩壊スターレイル" to "崩坏：星穹铁道", "崩坏：星穹铁道" to "崩坏：星穹铁道",
        "ウマ娘プリティーダービー" to "赛马娘", "赛马娘" to "赛马娘", "东方project" to "东方Project", "東方project" to "东方Project",
        "五等分の花嫁" to "五等分的新娘", "五等分的新娘" to "五等分的新娘",
    )

    fun normParody(p: String): String {
        val k = norm(p)
        return PARODY_ALIASES[k] ?: PARODY_ALIASES[k.replace(" ", "")] ?: toSimplified(p).trim()
    }

    // ---------- 标题里的设定词 ----------
    private val TITLE_HINTS: List<Pair<Regex, String>> = listOf(
        "メイド|女仆|女僕|maid" to "女仆",
        "催眠|さいみん" to "催眠",
        "お姉|姉さん|姉ちゃん|姐姐|姊姊|大姐|おねえ" to "姐姐",
        "(?<![辣姐姉姊兄黑太])妹(?![控])|いもうと" to "妹妹",
        "人妻|若妻|新妻|愛妻|爱妻|の妻|妻が|妻を|妻は|妻の" to "人妻",
        "寝取|寝とら|ネトラレ|ねとられ|ntr|出轨|出軌|绿帽" to "ntr",
        "幼馴染|幼なじみ|おさななじみ|青梅竹马|青梅竹馬" to "青梅竹马",
        "先輩|せんぱい|センパイ|前辈|前輩|学姐|學姐" to "前辈",
        "後輩|こうはい|コウハイ|后辈|後輩|学妹|學妹" to "后辈",
        "先生(?!方)|老师|老師|教師|教师" to "老师",
        "サキュバス|魅魔|淫魔" to "魅魔",
        "ギャル|辣妹" to "辣妹",
        "異世界|异世界" to "异世界",
        "義母|继母|繼母|お母さん|母さん|ママ(?!活)|妈妈|媽媽" to "母亲",
        "彼女(?!たち)|女友|女朋友|カノジョ" to "女友",
        "jk|女子校生|女子高生|女高中生" to "jk",
        "エルフ|精灵|精靈" to "精灵",
        "ナース|护士|護士|看護" to "护士",
        "上司|部下|ol(?![a-z])|女社员|女社員" to "职场",
    ).map { (p, t) -> rx(p, ci = true) to t }

    var useTitleHints = true
    private val titleTagCache = ConcurrentHashMap<String, List<String>>()

    fun titleTags(title: String?): List<String> {
        if (!useTitleHints || title.isNullOrEmpty()) return emptyList()
        return titleTagCache.getOrPut(title) {
            val core = parseTitle(title).core
            val s = lower(toSimplified(core))
            TITLE_HINTS.filter { (r, _) -> r.containsMatchIn(core) || r.containsMatchIn(s) }.map { it.second }.distinct()
        }
    }

    // 同义表：默认 + 跨站学到的 + 用户手动的（后者优先）
    @Volatile private var aliasTable: Map<String, String>? = null
    @Volatile var learnedAliases: Map<String, String> = emptyMap()
        set(v) { field = v; resetAliases() }
    @Volatile var userAliases: Map<String, String> = emptyMap()
        set(v) { field = v; resetAliases() }
    var useLearned = true

    private fun aliases(): Map<String, String> {
        aliasTable?.let { return it }
        val t = HashMap<String, String>()
        for ((k, v) in DEFAULT_ALIASES) t[norm(k)] = v
        if (useLearned) for ((k, v) in learnedAliases) t[norm(k)] = toSimplified(v)
        for ((k, v) in userAliases) t[norm(k)] = toSimplified(v)
        aliasTable = t
        return t
    }

    fun resetAliases() { aliasTable = null; tagCache.clear() }

    private val metaKeys: Set<String> by lazy { META_TAGS.map { norm(it) }.toSet() }
    private val tagCache = ConcurrentHashMap<String, String>()
    private const val NONE = "\u0000"

    fun normTag(t: String?): String? {
        val key = t ?: return null
        val r = tagCache.getOrPut(key) {
            val k = norm(key)
            if (k.isEmpty()) NONE else {
                val v = aliases()[k] ?: lower(toSimplified(key).trim())
                if (norm(v) in metaKeys) NONE else v
            }
        }
        return if (r == NONE) null else r
    }

    fun normTags(tags: List<String>?): List<String> {
        val out = ArrayList<String>()
        val seen = HashSet<String>()
        for (t in tags ?: emptyList()) {
            val n = normTag(t) ?: continue
            if (seen.add(n)) out.add(n)
        }
        return out
    }

    fun normPerson(name: String?): String = norm(name)

    // ---------- 标题拆解 ----------
    private val EVENT = rx(
        "^(c\\d{2,3}|comic\\s*1|comic1.*|紅楼夢.*|红楼梦.*|例大祭.*|コミティア.*|comitia.*|サンクリ.*|" +
            "cosmic.*|コミック.*|ふたけっと.*|gw超同人祭|.*オンリー.*|.*only.*|\\d{4}年.*)$", ci = true,
    )
    private val GROUP_HINT = rx("(汉化|漢化|翻译|翻譯|翻訳|个人|個人|嵌字|中文|重嵌|掃圖|扫图|机翻|機翻|工作室|练习|練習)")
    private val LANG_OR_FLAG = rx(
        "^(中国翻訳|中国翻译|中國翻譯|中国語|中國語|中国语|中文|chinese|english|日文|日语|日語|英訳|" +
            "dl版|無修正|无修正|全彩|カラー|彩色|digital|decensored|uncensored|重嵌|整合|雑誌|雜誌|雑誌寄せ集め|" +
            "中国翻訳\\s*.*|.*翻訳.*|.*翻譯.*|.*翻译.*)$", ci = true,
    )
    private val FLAG_TAGS = mapOf(
        "無修正" to "无修正", "无修正" to "无修正", "全彩" to "全彩", "カラー" to "全彩", "彩色" to "全彩", "uncensored" to "无修正",
        "decensored" to "无修正",
    )
    private val NOT_AUTHOR = setOf("pixiv", "fanbox", "fantia", "patreon", "skeb", "twitter", "ai generated", "ai", "中文", "全彩",
        "anthology", "アンソロジー", "雑誌", "杂志")
    private val BRACKETS = mapOf('[' to ']', '(' to ')', '【' to '】', '（' to '）', '「' to '」')
    private val REV_BRACKETS = BRACKETS.entries.associate { (k, v) -> v to k }

    class ParsedTitle(
        var core: String,
        val artists: MutableList<String> = ArrayList(),
        val circles: MutableList<String> = ArrayList(),
        var parodies: MutableList<String> = ArrayList(),
        val groups: MutableList<String> = ArrayList(),
        val flags: MutableList<String> = ArrayList(),
    )

    private fun takeLeading(s0: String): Triple<Char, String, String>? {
        val s = s0.trimStart()
        if (s.isEmpty() || s[0] !in BRACKETS) return null
        val open = s[0]
        val close = BRACKETS.getValue(open)
        var depth = 0
        for ((i, ch) in s.withIndex()) {
            if (ch == open) depth++ else if (ch == close) {
                depth--
                if (depth == 0) return Triple(open, s.substring(1, i).trim(), s.substring(i + 1))
            }
        }
        return null
    }

    private fun takeTrailing(s0: String): Triple<Char, String, String>? {
        val s = s0.trimEnd()
        if (s.isEmpty()) return null
        val last = s[s.length - 1]
        val opener = REV_BRACKETS[last] ?: return null
        var depth = 0
        for (i in s.length - 1 downTo 0) {
            val ch = s[i]
            if (ch == last) depth++ else if (ch == opener) {
                depth--
                if (depth == 0) return Triple(opener, s.substring(i + 1, s.length - 1).trim(), s.substring(0, i))
            }
        }
        return null
    }

    private val SPLIT_NAMES = rx("[、,，&＆×/]| x ")
    private fun splitNames(s: String): List<String> = SPLIT_NAMES.split(s).map { it.trim() }.filter { it.isNotEmpty() }

    private val CIRCLE_ARTIST = rx("^(.*?)\\s*[（(](.+)[）)]\\s*$")
    private fun circleArtist(inner: String, pt: ParsedTitle) {
        val m = CIRCLE_ARTIST.find(inner)
        if (m != null && m.groupValues[1].trim().isNotEmpty()) {
            pt.circles.addAll(splitNames(m.groupValues[1]))
            pt.artists.addAll(splitNames(m.groupValues[2]))
        } else pt.artists.addAll(splitNames(inner))
    }

    // Python 的 str.strip() 去的是 Unicode 空白；Kotlin 的 trim() 一样
    private val CHAPTER_P = rx("^(第?\\d+([-~]\\d+)?[话話卷巻]?|vol\\.?\\s*\\d+|上|中|下|前篇|后篇|後編|前編)$")
    private val CHAPTER_B = rx("^(第?\\d+[话話卷巻]?|vol\\.?\\s*\\d+|\\d+-\\d+)$")
    private val ORIGINAL = setOf("オリジナル", "original", "原创", "原創")

    private val parseCache = ConcurrentHashMap<String, ParsedTitle>()

    fun parseTitle(title: String?): ParsedTitle {
        val key = title ?: ""
        parseCache[key]?.let { return it }
        val r = doParse(key)
        if (parseCache.size > 60000) parseCache.clear()
        parseCache[key] = r
        return r
    }

    private fun doParse(title: String): ParsedTitle {
        var s = nfkc(title).trim()
        val pt = ParsedTitle(core = "")
        val leading = ArrayList<Pair<Char, String>>()
        while (true) {
            val r = takeLeading(s) ?: break
            leading.add(r.first to r.second)
            s = r.third
        }
        val trailing = ArrayList<Pair<Char, String>>()
        while (true) {
            val r = takeTrailing(s) ?: break
            if (r.third.trim().isEmpty()) break
            trailing.add(r.first to r.second)
            s = r.third
        }
        pt.core = s.trim().ifEmpty { if (leading.isNotEmpty()) leading.last().second else title }

        var slots = ArrayList<String>()
        for ((b, inner) in leading) {
            if (inner.isEmpty()) continue
            if ((b == '(' || b == '（') && EVENT.containsMatchIn(inner)) continue
            if ((b == '[' || b == '【') && GROUP_HINT.containsMatchIn(inner)) { pt.groups.add(inner); continue }
            if (b == '[' || b == '【') slots.add(inner)
        }
        slots = ArrayList(slots.filter { lower(it) !in NOT_AUTHOR })
        if (slots.isNotEmpty()) {
            circleArtist(slots.last(), pt)
            for (extra in slots.dropLast(1)) if (!LANG_OR_FLAG.containsMatchIn(extra)) pt.groups.add(extra)
        }
        for ((b, inner) in trailing) {
            if (inner.isEmpty()) continue
            val low = lower(inner)
            val flag = FLAG_TAGS[inner] ?: FLAG_TAGS[low]
            if (flag != null) { pt.flags.add(flag); continue }
            if (b == '(' || b == '（') {
                if (CHAPTER_P.containsMatchIn(low)) continue
                if (!LANG_OR_FLAG.containsMatchIn(inner) && !EVENT.containsMatchIn(inner)) pt.parodies.addAll(splitNames(inner))
            } else {
                if (LANG_OR_FLAG.containsMatchIn(inner)) continue
                if (GROUP_HINT.containsMatchIn(inner)) pt.groups.add(inner)
                else if (CHAPTER_B.containsMatchIn(low)) continue
                else pt.groups.add(inner)
            }
        }
        pt.parodies = ArrayList(pt.parodies.filter { lower(it) !in ORIGINAL })
        return pt
    }

    fun titleKey(title: String?): String = norm(parseTitle(title).core)

    private val SEG = rx("[\\[【(（「][^\\]】)）」]*[\\]】)）」]")

    fun dedupKeys(title: String?, authors: List<String>? = null): Set<String> {
        val pt = parseTitle(title)
        val keys = HashSet<String>()
        val core = norm(pt.core)
        val people = ((authors ?: emptyList()) + pt.artists + pt.circles).map { norm(it) }.filter { it.isNotEmpty() }.toSet()
        if (core.length >= 6) keys.add("t:$core")
        if (core.isNotEmpty()) for (a in people) keys.add("a:$a|$core")
        // 一本常挂几个译名：“中文名/日文名”“A | B”，每个都算
        val alts = nfkc(pt.core).split('/', '|', '／', '｜').map { norm(it) }.filter { it.length >= 6 && it != core }
        for (k in alts) { keys.add("t:$k"); for (a in people) keys.add("a:$a|$k") }
        for (seg in SEG.replace(nfkc(title ?: ""), "|").split("|")) {
            val k = norm(seg)
            if (k.length >= 6) keys.add("t:$k")
        }
        return keys
    }
}
