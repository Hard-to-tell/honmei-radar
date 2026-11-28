package honmei.app

import android.app.Application
import android.util.Log
import honmei.DB
import honmei.Env
import honmei.Server
import honmei.Service
import java.io.File
import java.io.IOException

/** 进程启动时把本地服务跑起来；页面只是 WebView 打开 http://127.0.0.1:端口/。 */
class HonmeiApplication : Application() {
    lateinit var service: Service
    var server: Server? = null

    override fun onCreate() {
        super.onCreate()
        val dir = File(filesDir, "data")
        dir.mkdirs()
        Env.log = { Log.i("honmei", it) }
        val db = DB(AndroidSql(File(dir, "shelf.db").path))
        Env.init(dir, db)
        service = Service(db)
        startServer()
        service.warmUp()
    }

    /** 优先用固定端口（页面里存的翻牌记录按端口区分），被占了就往后找。 */
    @Synchronized fun startServer(): Server {
        server?.takeIf { it.isAlive }?.let { return it }
        var last: IOException? = null
        for (port in listOf(8765, 8766, 8767, 8768, 0)) {
            try {
                val s = Server(service, port)
                s.start(fi.iki.elonen.NanoHTTPD.SOCKET_READ_TIMEOUT, true)
                server = s
                Env.logLine("本地服务已启动 127.0.0.1:${s.listeningPort}")
                return s
            } catch (e: IOException) { last = e }
        }
        throw last ?: IOException("本地服务起不来")
    }
}
