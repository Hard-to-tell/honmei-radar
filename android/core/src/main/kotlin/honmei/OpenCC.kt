package honmei

/** 繁转简：照搬 opencc-python-reimplemented 的 t2s（词组表 + 单字表，最长匹配），保证和电脑版的结果一致。 */
object OpenCC {
    private class Dict(val maxLen: Int, val minLen: Int, val map: Map<String, String>)

    private val dicts: List<Dict> by lazy { listOf(load("TSPhrases.txt"), load("TSCharacters.txt")) }

    private fun load(name: String): Dict {
        val m = HashMap<String, String>()
        var maxLen = 1
        var minLen = 1000
        val stream = OpenCC::class.java.getResourceAsStream("/honmei/data/opencc/$name") ?: return Dict(1, 1, m)
        stream.bufferedReader(Charsets.UTF_8).useLines { lines ->
            for (line in lines) {
                val parts = line.trim().split('\t')
                if (parts.size < 2) continue
                val k = parts[0]
                m[k] = parts[1]
                val l = k.codePointCount(0, k.length)
                if (l > maxLen) maxLen = l
                if (l < minLen) minLen = l
            }
        }
        return Dict(maxLen, minLen, m)
    }

    private val SPLIT = Regex("(\\s+|-|,|\\.|\\?|!|\\*|　|，|。|、|；|：|？|！|…|“|”|‘|’|『|』|「|」|﹁|﹂|—|－|（|）|《|》|〈|〉|～|．|／|＼|︒|︑|︔|︓|︿|﹀|︹|︺|︙|︐|［|﹇|］|﹈|︕|︖|︰|︳|︴|︽|︾|︵|︶|｛|︷|｝|︸|﹃|﹄|【|︻|】|︼)")

    fun convert(s: String): String {
        if (s.isEmpty()) return s
        val out = StringBuilder()
        var last = 0
        for (m in SPLIT.findAll(s)) {
            out.append(convertPart(s.substring(last, m.range.first)))
            out.append(m.value)
            last = m.range.last + 1
        }
        out.append(convertPart(s.substring(last)))
        return out.toString()
    }

    private class Node(var value: IntArray, var hint: Int?) {
        var left: Node? = null
        var right: Node? = null
    }

    private fun cps(s: String): IntArray = s.codePoints().toArray()
    private fun strOf(a: IntArray, from: Int = 0, to: Int = a.size) = String(a, from, to - from)

    private fun convertPart(s: String): String {
        if (s.isEmpty()) return s
        // 一组词典：先用词组表，没匹配上的片段再交给单字表
        val root = Node(cps(s), null)
        var working = ArrayDeque<Node>().apply { add(root) }
        var unmatched = ArrayDeque<Node>()
        for (d in dicts) {
            while (working.isNotEmpty()) {
                val cur = working.removeLast()
                val r = findMatch(cur.value, d, cur.hint)
                if (r != null) {
                    val (value, l, rr, testLen) = r
                    cur.value = value
                    cur.hint = null
                    if (l != null) { val n = Node(l, testLen); working.add(n); cur.left = n }
                    if (rr != null) { val n = Node(rr, testLen); working.add(n); cur.right = n }
                } else {
                    unmatched.add(cur)
                    cur.hint = null
                }
            }
            val t = working; working = unmatched; unmatched = t
        }
        val sb = StringBuilder()
        fun walk(n: Node?) {
            if (n == null) return
            walk(n.left); sb.append(strOf(n.value)); walk(n.right)
        }
        walk(root)
        return sb.toString()
    }

    private data class Match(val value: IntArray, val left: IntArray?, val right: IntArray?, val testLen: Int)

    private fun findMatch(a: IntArray, d: Dict, hint: Int?): Match? {
        val n = a.size
        var testLen = minOf(n, d.maxLen)
        if (hint != null && hint != 0) testLen = minOf(testLen, hint)
        while (testLen >= d.minLen) {
            for (i in 0..(n - testLen)) {
                val key = strOf(a, i, i + testLen)
                val v = d.map[key]
                if (v != null) {
                    val left = if (i > 0) a.copyOfRange(0, i) else null
                    val right = if (i + testLen < n) a.copyOfRange(i + testLen, n) else null
                    val value = v.split(' ')[0]
                    return Match(cps(value), left, right, testLen)
                }
            }
            testLen -= 1
        }
        return null
    }
}
