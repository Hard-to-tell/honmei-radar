package honmei

/** 极简 JSON：解析成 Map / List / String / Double / Long / Boolean / null，和 Python 的 dict/list 用法接近。 */
object Json {
    fun parse(s: String): Any? = Parser(s).run { val v = value(); ws(); v }

    fun stringify(v: Any?, indent: Int = 0): String = StringBuilder().also { write(it, v, indent, 0) }.toString()

    private fun write(sb: StringBuilder, v: Any?, indent: Int, level: Int) {
        when (v) {
            null -> sb.append("null")
            is String -> str(sb, v)
            is Boolean -> sb.append(v)
            is Int, is Long, is Short, is Byte -> sb.append(v.toString())
            is Double -> num(sb, v)
            is Float -> num(sb, v.toDouble())
            is Number -> sb.append(v.toString())
            is Map<*, *> -> {
                sb.append('{')
                var first = true
                for ((k, x) in v) {
                    if (!first) sb.append(',')
                    first = false
                    nl(sb, indent, level + 1)
                    str(sb, k.toString()); sb.append(if (indent > 0) ": " else ":")
                    write(sb, x, indent, level + 1)
                }
                if (!first) nl(sb, indent, level)
                sb.append('}')
            }
            is Iterable<*> -> {
                sb.append('[')
                var first = true
                for (x in v) {
                    if (!first) sb.append(',')
                    first = false
                    nl(sb, indent, level + 1)
                    write(sb, x, indent, level + 1)
                }
                if (!first) nl(sb, indent, level)
                sb.append(']')
            }
            is Array<*> -> write(sb, v.toList(), indent, level)
            is DoubleArray -> write(sb, v.toList(), indent, level)
            is IntArray -> write(sb, v.toList(), indent, level)
            is Pair<*, *> -> write(sb, listOf(v.first, v.second), indent, level)
            else -> str(sb, v.toString())
        }
    }

    private fun nl(sb: StringBuilder, indent: Int, level: Int) {
        if (indent > 0) { sb.append('\n'); repeat(indent * level) { sb.append(' ') } }
    }

    private fun num(sb: StringBuilder, d: Double) {
        if (d.isNaN() || d.isInfinite()) { sb.append("null"); return }
        if (d == Math.rint(d) && Math.abs(d) < 1e15) sb.append(d.toLong().toString()).append(".0")
        else sb.append(d.toString())
    }

    private fun str(sb: StringBuilder, s: String) {
        sb.append('"')
        for (c in s) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                '\b' -> sb.append("\\b")
                '\u000c' -> sb.append("\\f")
                else -> if (c < ' ' || c == ' ' || c == ' ') sb.append(String.format("\\u%04x", c.code)) else sb.append(c)
            }
        }
        sb.append('"')
    }

    private class Parser(val s: String) {
        var i = 0
        fun ws() { while (i < s.length && s[i].isWhitespace()) i++ }
        fun value(): Any? {
            ws()
            if (i >= s.length) throw IllegalArgumentException("JSON 结束得太早")
            return when (val c = s[i]) {
                '{' -> obj()
                '[' -> arr()
                '"' -> string()
                't' -> lit("true", true)
                'f' -> lit("false", false)
                'n' -> lit("null", null)
                'N' -> lit("NaN", Double.NaN)
                else -> if (c == '-' || c.isDigit()) number() else throw IllegalArgumentException("JSON 解析失败：位置 $i 的 '$c'")
            }
        }
        fun lit(w: String, v: Any?): Any? {
            if (!s.startsWith(w, i)) throw IllegalArgumentException("JSON 解析失败：位置 $i")
            i += w.length; return v
        }
        fun obj(): Map<String, Any?> {
            val m = LinkedHashMap<String, Any?>()
            i++; ws()
            if (s[i] == '}') { i++; return m }
            while (true) {
                ws(); val k = string(); ws()
                if (s[i] != ':') throw IllegalArgumentException("JSON 解析失败：缺少冒号 $i")
                i++
                m[k] = value(); ws()
                when (s[i]) { ',' -> i++; '}' -> { i++; return m }; else -> throw IllegalArgumentException("JSON 解析失败：$i") }
            }
        }
        fun arr(): List<Any?> {
            val l = ArrayList<Any?>()
            i++; ws()
            if (s[i] == ']') { i++; return l }
            while (true) {
                l.add(value()); ws()
                when (s[i]) { ',' -> i++; ']' -> { i++; return l }; else -> throw IllegalArgumentException("JSON 解析失败：$i") }
            }
        }
        fun string(): String {
            if (s[i] != '"') throw IllegalArgumentException("JSON 解析失败：应为字符串 $i")
            i++
            val sb = StringBuilder()
            while (true) {
                val c = s[i++]
                when (c) {
                    '"' -> return sb.toString()
                    '\\' -> {
                        when (val e = s[i++]) {
                            'n' -> sb.append('\n'); 't' -> sb.append('\t'); 'r' -> sb.append('\r')
                            'b' -> sb.append('\b'); 'f' -> sb.append('\u000c')
                            'u' -> { sb.append(s.substring(i, i + 4).toInt(16).toChar()); i += 4 }
                            else -> sb.append(e)
                        }
                    }
                    else -> sb.append(c)
                }
            }
        }
        fun number(): Any {
            val st = i
            if (s[i] == '-') i++
            while (i < s.length && (s[i].isDigit() || s[i] in ".eE+-")) i++
            val t = s.substring(st, i)
            return if (t.any { it in ".eE" }) t.toDouble() else t.toLongOrNull() ?: t.toDouble()
        }
    }
}

// ---- 动态取值小工具 ----
@Suppress("UNCHECKED_CAST")
fun Any?.obj(): Map<String, Any?> = (this as? Map<String, Any?>) ?: emptyMap()
@Suppress("UNCHECKED_CAST")
fun Any?.arr(): List<Any?> = (this as? List<Any?>) ?: emptyList()
fun Any?.str(): String = when (this) { null -> ""; is String -> this; is Double -> if (this == Math.rint(this)) this.toLong().toString() else this.toString(); else -> this.toString() }
fun Any?.dbl(): Double = when (this) { is Number -> this.toDouble(); is String -> this.toDoubleOrNull() ?: 0.0; is Boolean -> if (this) 1.0 else 0.0; else -> 0.0 }
fun Any?.int(): Int = when (this) { is Number -> this.toInt(); is String -> this.toDoubleOrNull()?.toInt() ?: 0; is Boolean -> if (this) 1 else 0; else -> 0 }
fun Any?.long(): Long = when (this) { is Number -> this.toLong(); is String -> this.toDoubleOrNull()?.toLong() ?: 0L; else -> 0L }
fun Any?.bool(): Boolean = when (this) { null -> false; is Boolean -> this; is Number -> this.toDouble() != 0.0; is String -> this.isNotEmpty(); is Collection<*> -> this.isNotEmpty(); is Map<*, *> -> this.isNotEmpty(); else -> true }
fun Any?.strList(): List<String> = this.arr().map { it.str() }
