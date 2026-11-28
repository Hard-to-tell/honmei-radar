package honmei

import java.io.File

/** 手动看页面用：HONMEI_TEST_DB=... 跑 ServeMainKt，浏览器打开 http://127.0.0.1:8799/?k=dev */
fun main() {
    val src = System.getenv("HONMEI_TEST_DB") ?: error("set HONMEI_TEST_DB")
    val dir = File(System.getenv("HONMEI_TEST_DIR") ?: (System.getProperty("java.io.tmpdir") + "/honmei-serve")).apply { mkdirs() }
    val dbf = File(dir, "shelf.db"); if (!dbf.exists()) File(src).copyTo(dbf)
    val db = DB(JdbcSql(dbf.path)); Env.init(dir, db)
    val svc = Service(db)
    Server(svc, 8799, "dev").start(fi.iki.elonen.NanoHTTPD.SOCKET_READ_TIMEOUT, false)
    println("serving"); Thread.sleep(Long.MAX_VALUE)
}
