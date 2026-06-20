(function(){

  function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]||c;});}
  function copyToken(token, statusEl){
    function done(){ if(statusEl) statusEl.textContent="Token copiat in clipboard. Revino in pagina helperului si apasa Continua in Home Assistant."; }
    if(navigator.clipboard && navigator.clipboard.writeText){navigator.clipboard.writeText(token).then(done).catch(done);}
    else { try{document.execCommand("copy");}catch(e){} done(); }
  }
  function showBox(token){
    var old=document.getElementById("cr-token-helper-overlay"); if(old) old.remove();
    var overlay=document.createElement("div");
    overlay.id="cr-token-helper-overlay";
    overlay.style.cssText="position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.55);font-family:Arial,sans-serif;color:#111";
    var box=document.createElement("div");
    box.style.cssText="max-width:760px;margin:6vh auto;background:#fff;border-radius:14px;padding:20px;box-shadow:0 20px 60px rgba(0,0,0,.35)";
    box.innerHTML='<h2 style="margin:0 0 10px;font-size:20px">Curieri Romania - token Sameday</h2>'+ 
      '<p style="margin:0 0 12px;color:#444">Refresh tokenul a fost gasit si copiat in clipboard. Revino in pagina helperului Curieri Romania, apasa <b>Continua in Home Assistant</b>, apoi lipeste tokenul in formular.</p>'+ 
      '<textarea id="cr-token-helper-value" readonly style="width:100%;height:150px;box-sizing:border-box;font-family:monospace;font-size:12px;border:1px solid #ccc;border-radius:8px;padding:10px">'+esc(token)+'</textarea>'+ 
      '<div style="display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap">'+
      '<button id="cr-token-helper-copy" style="padding:10px 14px;border:0;border-radius:8px;background:#0b57d0;color:#fff;cursor:pointer">Copiaza din nou tokenul</button>'+ 
      '<button id="cr-token-helper-close" style="padding:10px 14px;border:1px solid #bbb;border-radius:8px;background:#fff;cursor:pointer">Inchide</button>'+ 
      '<span id="cr-token-helper-status" style="color:#444"></span></div>'+ 
      '<p style="margin:12px 0 0;color:#777;font-size:12px">Tokenul este citit local din pagina curierului si nu este trimis automat nicaieri.</p>';
    overlay.appendChild(box); document.body.appendChild(overlay);
    var ta=document.getElementById("cr-token-helper-value"); var st=document.getElementById("cr-token-helper-status");
    ta.focus(); ta.select();
    document.getElementById("cr-token-helper-copy").onclick=function(){copyToken(token, st);};
    document.getElementById("cr-token-helper-close").onclick=function(){overlay.remove();};
    copyToken(token, st);
  }


  function findToken(){
    var key = "oidc.user:https://identity.sameday.ro/:Mobile_Web_Client";
    var raw = sessionStorage.getItem(key);
    if (raw) {
      try {
        var obj = JSON.parse(raw);
        if (obj && obj.refresh_token) return obj.refresh_token;
      } catch(e) {}
    }
    for (var i=0;i<sessionStorage.length;i++){
      var k=sessionStorage.key(i)||"";
      if (k.toLowerCase().indexOf("oidc.user") === -1) continue;
      try {
        var item=JSON.parse(sessionStorage.getItem(k)||"{}");
        if (item && item.refresh_token) return item.refresh_token;
      } catch(e) {}
    }
    return "";
  }

  if(location.hostname.toLowerCase()!=="sameday.ro"){alert("Deschide mai intai https://sameday.ro/trimite-colete, autentifica-te, apoi apasa bookmarkletul."); return;}
  var token=findToken();
  if(!token){alert("Nu am gasit refresh token. Verifica daca esti logat, reincarca pagina si incearca din nou."); return;}
  showBox(token);
})();