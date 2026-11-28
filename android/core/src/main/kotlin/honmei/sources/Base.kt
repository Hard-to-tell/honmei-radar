package honmei.sources

import honmei.Norm
import honmei.Progress
import honmei.Work

/** 站点适配器接口（对应 sources/base.py）。 */
interface SiteAdapter {
    val site: String
    val name: String
    fun login(username: String, password: String): String
    fun loggedIn(): Boolean
    fun fetchFavorites(progress: Progress?, maxPages: Int? = null): List<Work>
    fun fetchDetail(wid: String): Pair<Work, List<Work>>
    fun search(query: String, mode: String = "site", page: Int = 1, order: String = "latest"): List<Work> = throw NotImplementedError()
    fun ranking(kind: String = "week", page: Int = 1): List<Work> = throw NotImplementedError()
    fun browse(page: Int = 1, category: String = "all", order: String = "latest"): List<Work> = throw NotImplementedError()
    val browseCategories: List<String> get() = listOf("all")
    fun webUrl(wid: String): String
    fun coverUrls(w: Work): List<String> = if (w.cover.isNotEmpty()) listOf(w.cover) else emptyList()
}

/** 把标题里的作者、社团、原作、标记补进字段（不覆盖已有值）。 */
fun enrichFromTitle(w: Work): Work {
    val pt = Norm.parseTitle(w.title)
    fun add(dst: MutableList<String>, items: List<String>) {
        val low = dst.map { it.lowercase() }.toMutableSet()
        for (it in items) if (it.isNotEmpty() && low.add(it.lowercase())) dst.add(it)
    }
    add(w.authors, pt.artists)
    add(w.circles, pt.circles)
    add(w.parodies, pt.parodies)
    add(w.groups, pt.groups)
    add(w.tags, pt.flags)
    return w
}

val SITES = linkedMapOf("jm" to "禁漫", "pica" to "哔咔")
