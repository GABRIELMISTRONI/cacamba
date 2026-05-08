(function () {
  'use strict';

  // ── Theme Toggle (Dark/Light Mode) ─────────────────
  (function() {
    var themeToggle = document.getElementById('theme-toggle');
    var themeIcon = document.getElementById('theme-icon');
    if (!themeToggle) return;
    
    function setTheme(dark) {
      document.body.classList.toggle('dark', dark);
      themeIcon.textContent = dark ? '☀️' : '🌙';
      localStorage.setItem('theme', dark ? 'dark' : 'light');
    }
    
    var saved = localStorage.getItem('theme');
    if (saved === 'dark' || (!saved && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
      setTheme(true);
    }
    
    themeToggle.addEventListener('click', function() {
      setTheme(!document.body.classList.contains('dark'));
    });
  })();

  // ── Modal System ────────────────────────────────
  window.showModal = function(title, message, onConfirm) {
    var backdrop = document.getElementById('modal-backdrop');
    var modal = document.getElementById('modal');
    var body = document.getElementById('modal-body');
    var confirmBtn = document.getElementById('modal-confirm');
    
    body.innerHTML = '<h3 style="margin-top:0">' + title + '</h3><p>' + message + '</p>';
    backdrop.style.display = 'block';
    modal.style.display = 'block';
    
    confirmBtn.onclick = function() {
      closeModal();
      if (onConfirm) onConfirm();
    };
  };

  window.closeModal = function() {
    document.getElementById('modal-backdrop').style.display = 'none';
    document.getElementById('modal').style.display = 'none';
  };

  // ── Loading Overlay ──────────────────────────────
  window.showLoading = function() {
    var existing = document.getElementById('loading-overlay');
    if (existing) return;
    var overlay = document.createElement('div');
    overlay.id = 'loading-overlay';
    overlay.className = 'loading-overlay';
    document.body.appendChild(overlay);
  };

  window.hideLoading = function() {
    var overlay = document.getElementById('loading-overlay');
    if (overlay) overlay.remove();
  };

  // ── Masks for inputs ────────────────────────────
  function digits(value, max) {
    var clean = String(value || '').replace(/\D/g, '');
    return max ? clean.slice(0, max) : clean;
  }

  function maskCep(value) {
    var d = digits(value, 8);
    if (d.length > 5) return d.slice(0, 5) + '-' + d.slice(5);
    return d;
  }

  function maskCpf(value) {
    var d = digits(value, 11);
    var out = d;
    if (d.length > 9) {
      out = d.slice(0, 3) + '.' + d.slice(3, 6) + '.' + d.slice(6, 9) + '-' + d.slice(9);
    } else if (d.length > 6) {
      out = d.slice(0, 3) + '.' + d.slice(3, 6) + '.' + d.slice(6);
    } else if (d.length > 3) {
      out = d.slice(0, 3) + '.' + d.slice(3);
    }
    return out;
  }

  function maskPhone(value) {
    var d = digits(value, 11);
    if (d.length > 10) {
      return '(' + d.slice(0, 2) + ') ' + d.slice(2, 7) + '-' + d.slice(7);
    }
    if (d.length > 6) {
      return '(' + d.slice(0, 2) + ') ' + d.slice(2, 6) + '-' + d.slice(6);
    }
    if (d.length > 2) {
      return '(' + d.slice(0, 2) + ') ' + d.slice(2);
    }
    if (d.length > 0) {
      return '(' + d;
    }
    return '';
  }

  function maskCnpj(value) {
    var d = digits(value, 14);
    if (d.length > 12) return d.slice(0,2)+'.'+d.slice(2,5)+'.'+d.slice(5,8)+'/'+d.slice(8,12)+'-'+d.slice(12);
    if (d.length > 8)  return d.slice(0,2)+'.'+d.slice(2,5)+'.'+d.slice(5,8)+'/'+d.slice(8);
    if (d.length > 5)  return d.slice(0,2)+'.'+d.slice(2,5)+'.'+d.slice(5);
    if (d.length > 2)  return d.slice(0,2)+'.'+d.slice(2);
    return d;
  }

  function applyMask(input) {
    var kind = input.dataset.mask;
    if (kind === 'cep')  input.value = maskCep(input.value);
    if (kind === 'cpf')  input.value = maskCpf(input.value);
    if (kind === 'cnpj') input.value = maskCnpj(input.value);
    if (kind === 'phone') input.value = maskPhone(input.value);
  }

  // ── CEP Autofill ──────────────────────────────────
  function setCepStatus(text, state) {
    var el = document.querySelector('[data-cep-status]');
    if (!el) return;
    el.textContent = text || '';
    el.dataset.state = state || '';
  }

  async function autofillCep(input) {
    var d = digits(input.value, 8);
    if (d.length !== 8) {
      setCepStatus('', '');
      return;
    }

    var isCliente = input.id === 'cep';
    var ruaId = isCliente ? 'rua' : 'obra_rua';
    var bairroId = isCliente ? 'bairro' : 'obra_bairro';

    var rua = document.getElementById(ruaId);
    var bairro = document.getElementById(bairroId);
    if (!rua || !bairro) return;

    setCepStatus('Buscando endereço...', 'loading');
    try {
      var resp = await fetch('https://viacep.com.br/ws/' + d + '/json/');
      if (!resp.ok) {
        setCepStatus('Não foi possível buscar o CEP agora.', 'error');
        return;
      }
      var dados = await resp.json();
      if (dados.erro) {
        setCepStatus('CEP não encontrado.', 'error');
        return;
      }
      if (!rua.value && dados.logradouro) rua.value = dados.logradouro;
      if (!bairro.value) {
        if (dados.bairro) {
          bairro.value = dados.bairro;
        } else if (dados.logradouro) {
          var logradouro = dados.logradouro.toLowerCase();
          if (logradouro.includes('rua') || logradouro.includes('avenida') || logradouro.includes('alameda')) {
            bairro.value = 'Centro';
          } else {
            bairro.value = dados.logradouro;
          }
        }
      }
      setCepStatus('Endereço preenchido pelo CEP.', 'ok');
      
      var nextField = document.getElementById(isCliente ? 'numero' : 'obra_numero');
      if (nextField) nextField.focus();
    } catch (e) {
      setCepStatus('Busca de CEP indisponível. Preencha manualmente.', 'error');
    }
  }

  // Initialize masks
  document.querySelectorAll('[data-mask]').forEach(function (input) {
    applyMask(input);
    input.addEventListener('input', function () {
      applyMask(input);
    });
  });

  // Initialize CEP autofill
  document.querySelectorAll('[data-cep-autofill]').forEach(function (input) {
    input.addEventListener('blur', function () {
      applyMask(input);
      autofillCep(input);
    });
  });

  // Form submission loading state
  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      form.querySelectorAll('[data-mask]').forEach(applyMask);
      showLoading();
      var button = form.querySelector('button[type="submit"]');
      if (button) {
        button.classList.add('is-loading');
        button.disabled = true;
      }
    });
  });

  // Confirm dialogs
  document.querySelectorAll('[data-confirm]').forEach(function (el) {
    el.addEventListener('click', function (e) {
      if (!confirm(el.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });

  // Dynamic address loading for new order
  var clienteSelect = document.getElementById('cliente_id');
  var enderecoSelect = document.getElementById('endereco_id');
  var previewDiv = document.getElementById('preview-endereco');
  var formNovoDiv = document.getElementById('form-novo-endereco');
  var addresses = [];

  function fillObraAddress(addr) {
    ['obra_cep','obra_rua','obra_quadra','obra_numero','obra_bairro'].forEach(function(id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.value = addr[id.replace('obra_','')] || '';
    });
  }

  function showPreview(addr) {
    if (!previewDiv) return;
    fillObraAddress(addr);
    previewDiv.innerHTML = 
      '<div class="end-preview-box">' +
        (addr.apelido ? '<strong class="end-apelido">📍 ' + esc(addr.apelido) + '</strong>' : '') +
        '<div>' + esc(addr.rua) +
        (addr.quadra ? ', Q.' + esc(addr.quadra) : '') +
        (addr.numero ? ', nº ' + esc(addr.numero) : '') +
        (addr.bairro ? ' — ' + esc(addr.bairro) : '') +
        '</div>' +
        (addr.cep && addr.cep.length === 8 ? '<small class="muted mono">CEP ' + addr.cep.slice(0,5) + '-' + addr.cep.slice(5) + '</small>' : '') +
        (addr.complemento ? '<small class="muted">' + esc(addr.complemento) + '</small>' : '') +
      '</div>';
    previewDiv.style.display = 'block';
    if (formNovoDiv) formNovoDiv.style.display = 'block';
  }

  function showFormNovo() {
    if (previewDiv) previewDiv.style.display = 'none';
    if (formNovoDiv) formNovoDiv.style.display = 'block';
  }

  function clearFormNovo() {
    ['obra_cep', 'obra_rua', 'obra_quadra', 'obra_numero', 'obra_bairro', 'endereco_complemento', 'endereco_apelido'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.value = '';
    });
  }

  function populateAddressList(data) {
    if (!enderecoSelect) return;
    addresses = data || [];
    
    if (!addresses.length) {
      enderecoSelect.innerHTML = '<option value="novo">+ Adicionar novo endereço</option>';
      clearFormNovo();
      showFormNovo();
      return;
    }

    enderecoSelect.innerHTML = '<option value="">— Escolha o endereço —</option>';
    addresses.forEach(function(a) {
      var label = a.apelido ? '[' + a.apelido + '] ' : '';
      label += a.rua;
      if (a.quadra) label += ', Q.' + a.quadra;
      if (a.numero) label += ', nº ' + a.numero;
      if (a.bairro) label += ' — ' + a.bairro;
      enderecoSelect.innerHTML += '<option value="' + a.id + '">' + label + '</option>';
    });
    enderecoSelect.innerHTML += '<option value="novo">+ Adicionar novo endereço</option>';
    
    if (previewDiv) previewDiv.style.display = 'none';
    if (formNovoDiv) formNovoDiv.style.display = 'block';
  }

  function esc(s) {
    if (s === null || s === undefined) return '';
    var t = document.createElement('div');
    t.textContent = String(s);
    return t.innerHTML;
  }

  if (enderecoSelect) {
    enderecoSelect.addEventListener('change', function() {
      var val = enderecoSelect.value;
      if (val === 'novo') {
        clearFormNovo();
        showFormNovo();
        return;
      }
      if (!val) {
        if (previewDiv) previewDiv.style.display = 'none';
        if (formNovoDiv) formNovoDiv.style.display = 'none';
        return;
      }
      var addr = addresses.find(function(a){ return String(a.id) === val; });
      if (addr) showPreview(addr);
    });
  }

  if (clienteSelect) {
    clienteSelect.addEventListener('change', function() {
      var cid = clienteSelect.value;
      if (!cid) {
        if (enderecoSelect) enderecoSelect.innerHTML = '<option value="">— Selecione o cliente primeiro —</option>';
        if (previewDiv) previewDiv.style.display = 'none';
        if (formNovoDiv) formNovoDiv.style.display = 'none';
        return;
      }
      showLoading();
      fetch('/api/clientes/' + cid + '/enderecos')
        .then(function(r){ return r.ok ? r.json() : []; })
        .then(function(data) {
          hideLoading();
          populateAddressList(data);
        })
        .catch(function(){ 
          hideLoading();
          if (enderecoSelect) {
            enderecoSelect.innerHTML = '<option value="novo">+ Adicionar novo endereço</option>'; 
            showFormNovo(); 
          }
        });
    });
  }

  // Load available dumpsters via API
  document.querySelectorAll('.select-cacamba').forEach(function(sel) {
    var cap = sel.dataset.cap;
    if (!cap) return;
    
    showLoading();
    fetch('/api/cacambas-disponiveis?capacidade=' + cap)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        hideLoading();
        if (data.length) {
          sel.innerHTML = '<option value="">Escolha</option>' + 
            data.map(function(c) { 
              return '<option value="' + c.id + '">Nº ' + c.codigo + ' (' + c.capacidade_m3 + 'm³)</option>'; 
            }).join('');
        } else {
          sel.innerHTML = '<option value="">Sem disponível (' + cap + 'm³)</option>';
        }
      })
      .catch(function() { 
        hideLoading();
        sel.innerHTML = '<option value="">Erro ao carregar</option>'; 
      });
  });

  // Initialize on page load
  var initClient = document.body.dataset.preClienteId || '';
  if (initClient && clienteSelect) {
    showLoading();
    fetch('/api/clientes/' + initClient + '/enderecos')
      .then(function(r){ return r.ok ? r.json() : []; })
      .then(function(data) {
        hideLoading();
        populateAddressList(data);
      })
      .catch(function(){ 
        hideLoading();
        if (enderecoSelect) {
          enderecoSelect.innerHTML = '<option value="novo">+ Adicionar novo endereço</option>'; 
          showFormNovo(); 
        }
      });
  }

  // Table row click navigation
  document.querySelectorAll('.linha-clicavel').forEach(function(row) {
    row.addEventListener('click', function() {
      var href = row.dataset.href || row.querySelector('a')?.href;
      if (href) window.location.href = href;
    });
  });

  // Auto-hide flash messages
  setTimeout(function() {
    document.querySelectorAll('.flash').forEach(function(flash) {
      flash.style.transition = 'opacity 0.5s ease';
      flash.style.opacity = '0';
      setTimeout(function() { flash.remove(); }, 500);
    });
  }, 5000);

})();