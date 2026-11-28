package honmei

/** 两站作品的统一表示（对应 models.py）。 */
data class Work(
    val site: String,
    val wid: String,
    var title: String,
    var authors: MutableList<String> = ArrayList(),
    var tags: MutableList<String> = ArrayList(),
    var parodies: MutableList<String> = ArrayList(),
    var circles: MutableList<String> = ArrayList(),
    var groups: MutableList<String> = ArrayList(),
    var category: String = "",
    var description: String = "",
    var cover: String = "",
    var url: String = "",
    var popularity: Double = 0.0,
    var detailOk: Boolean = false,
) {
    val key: Pair<String, String> get() = site to wid
}

class SiteError(msg: String, cause: Throwable? = null) : Exception(msg, cause)

typealias Progress = (msg: String, done: Int?, total: Int?) -> Unit

fun Progress?.say(msg: String, done: Int? = null, total: Int? = null) { this?.invoke(msg, done, total) }

fun now(): Double = System.currentTimeMillis() / 1000.0
