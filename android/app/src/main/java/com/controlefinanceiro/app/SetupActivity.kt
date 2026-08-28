package com.controlefinanceiro.app

import android.content.Intent
import android.os.Bundle
import android.text.TextUtils
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.text.HtmlCompat
import kotlin.concurrent.thread

/**
 * Primeira tela: pergunta onde está o servidor.
 *
 * Além de conectar, ela testa. Quem instala o app na casa de outra pessoa
 * precisa saber se o problema é o endereço, a rede, o certificado ou o
 * servidor — e o botão de testar mostra isso etapa por etapa, sem salvar nada.
 *
 * É também a tela que mostra o motivo quando o app fechou sozinho na abertura
 * anterior: é a única em que dá para ler o recado com calma, já que a tela
 * principal, nesse caso, é justamente a que não abre.
 */
class SetupActivity : AppCompatActivity() {

    private lateinit var campo: EditText
    private lateinit var botao: Button
    private lateinit var botaoTestar: Button
    private lateinit var aviso: TextView
    private lateinit var carregando: ProgressBar
    private lateinit var resultado: TextView
    private lateinit var painelFalha: View
    private lateinit var textoFalha: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)

        campo = findViewById(R.id.campoServidor)
        botao = findViewById(R.id.botaoConectar)
        botaoTestar = findViewById(R.id.botaoTestar)
        aviso = findViewById(R.id.avisoSetup)
        carregando = findViewById(R.id.carregandoSetup)
        resultado = findViewById(R.id.resultadoTeste)
        painelFalha = findViewById(R.id.painelFalha)
        textoFalha = findViewById(R.id.textoFalha)

        // Se já existe endereço salvo, chegamos aqui para trocá-lo.
        Servidor.url(this)?.let { campo.setText(it) }

        botao.setOnClickListener { conectar() }
        botaoTestar.setOnClickListener { testar() }
        campo.setOnEditorActionListener { _, _, _ -> conectar(); true }

        mostrarFalhaAnterior()
    }

    private fun mostrarFalhaAnterior() {
        val falha = RegistroDeFalhas.ultima(this)
        if (falha == null) {
            painelFalha.visibility = View.GONE
            return
        }
        textoFalha.text = falha
        painelFalha.visibility = View.VISIBLE
        findViewById<Button>(R.id.botaoDispensarFalha).setOnClickListener {
            RegistroDeFalhas.limpar(this)
            painelFalha.visibility = View.GONE
        }
    }

    private fun mostrarAviso(texto: String) {
        aviso.text = texto
        aviso.visibility = View.VISIBLE
    }

    private fun ocupado(sim: Boolean, textoDoBotao: Int) {
        botao.isEnabled = !sim
        botaoTestar.isEnabled = !sim
        campo.isEnabled = !sim
        carregando.visibility = if (sim) View.VISIBLE else View.GONE
        botao.text = getString(if (sim) textoDoBotao else R.string.setup_conectar)
        botaoTestar.text = getString(
            if (sim) R.string.setup_testando else R.string.setup_testar
        )
    }

    /** Testa e mostra tudo, sem salvar o endereço. */
    private fun testar() {
        aviso.visibility = View.GONE
        resultado.visibility = View.GONE
        val digitado = campo.text.toString()
        ocupado(true, R.string.setup_conectar)
        thread {
            val relatorio = Diagnostico.testar(applicationContext, digitado)
            runOnUiThread {
                ocupado(false, R.string.setup_conectar)
                exibir(relatorio)
            }
        }
    }

    /** Testa e, se tudo passou, salva e entra. */
    private fun conectar() {
        aviso.visibility = View.GONE
        resultado.visibility = View.GONE
        val digitado = campo.text.toString()
        ocupado(true, R.string.setup_conectando)
        thread {
            val relatorio = Diagnostico.testar(applicationContext, digitado)
            runOnUiThread {
                ocupado(false, R.string.setup_conectando)
                val endereco = relatorio.endereco
                if (relatorio.passou && endereco != null) {
                    // O endereço novo passou: o que estava registrado é história.
                    RegistroDeFalhas.limpar(this)
                    Servidor.salvar(this, endereco)
                    startActivity(Intent(this, MainActivity::class.java))
                    finish()
                } else {
                    // Mostra o relatório inteiro, não só a última linha: a etapa
                    // que parou só faz sentido junto com as que passaram.
                    mostrarAviso(getString(R.string.setup_nao_deu))
                    exibir(relatorio)
                }
            }
        }
    }

    private fun exibir(relatorio: Diagnostico.Relatorio) {
        val html = StringBuilder()
        relatorio.etapas.forEach { etapa ->
            val (cor, marca) = when (etapa.situacao) {
                Diagnostico.Situacao.OK -> "#00a76f" to "&#10003;"
                Diagnostico.Situacao.AVISO -> "#ffab00" to "!"
                Diagnostico.Situacao.FALHA -> "#ff5630" to "&#10007;"
            }
            html.append("<font color='$cor'><b>$marca ${TextUtils.htmlEncode(etapa.titulo)}</b></font><br/>")
            html.append("<font color='#919eab'>${TextUtils.htmlEncode(etapa.detalhe)}</font><br/><br/>")
        }
        val fecho = if (relatorio.passou) {
            "<font color='#00a76f'><b>Comunicação em ordem.</b></font>"
        } else {
            "<font color='#ff5630'><b>O teste parou na etapa marcada acima.</b></font>"
        }
        html.append(fecho)

        resultado.text = HtmlCompat.fromHtml(html.toString(), HtmlCompat.FROM_HTML_MODE_LEGACY)
        resultado.visibility = View.VISIBLE
    }
}
