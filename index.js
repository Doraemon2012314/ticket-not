// Cloudflare Worker Discord Ticket Bot
// Deploy this entire code to Cloudflare Workers

// Environment variables (set in Cloudflare dashboard)
// DISCORD_TOKEN - Your bot token
// DISCORD_PUBLIC_KEY - Your public key from Discord Developer Portal

// Discord API endpoint
const DISCORD_API = 'https://discord.com/api/v10';

// Helper function to make Discord API calls
async function discordRequest(endpoint, method = 'GET', body = null) {
  const headers = {
    'Authorization': `Bot ${DISCORD_TOKEN}`,
    'Content-Type': 'application/json'
  };
  
  const options = {
    method,
    headers
  };
  
  if (body) {
    options.body = JSON.stringify(body);
  }
  
  try {
    const response = await fetch(`${DISCORD_API}${endpoint}`, options);
    return await response.json();
  } catch (error) {
    console.error(`Discord API error: ${error}`);
    return null;
  }
}

// Get guild configuration from KV storage
async function getGuildConfig(guildId, env) {
  try {
    const config = await env.TICKET_KV.get(`config:${guildId}`, 'json');
    if (!config) {
      return {
        ticket_category_id: null,
        staff_role_id: null,
        admin_role_id: null,
        transcript_channel_id: null,
        created_at: Date.now()
      };
    }
    return config;
  } catch (error) {
    console.error(`Error getting config: ${error}`);
    return {
      ticket_category_id: null,
      staff_role_id: null,
      admin_role_id: null,
      transcript_channel_id: null
    };
  }
}

// Save guild configuration to KV storage
async function saveGuildConfig(guildId, config, env) {
  try {
    await env.TICKET_KV.put(`config:${guildId}`, JSON.stringify(config));
    return true;
  } catch (error) {
    console.error(`Error saving config: ${error}`);
    return false;
  }
}

// Handle /setup command - Deploy ticket panel
async function handleSetup(interaction, env) {
  const guildId = interaction.guild_id;
  const channelId = interaction.channel_id;
  const config = await getGuildConfig(guildId, env);
  
  if (!config.ticket_category_id) {
    return {
      type: 4,
      data: {
        content: '❌ Please run `/settings` first to configure the ticket category!\n\n**How to get category ID:**\n1. Enable Developer Mode in Discord\n2. Right-click on your ticket category\n3. Click "Copy ID"\n4. Use `/settings` to set it',
        flags: 64
      }
    };
  }
  
  // Create ticket panel embed
  const embed = {
    title: "🎫 Support Ticket System",
    description: "Welcome to our support system! Please click the button below to create a ticket.",
    color: 0x5865f2,
    fields: [
      {
        name: "📋 How It Works",
        value: "1️⃣ Click the **Create Ticket** button below\n2️⃣ Select a category\n3️⃣ Describe your issue\n4️⃣ Staff will assist you shortly",
        inline: false
      },
      {
        name: "📌 Guidelines",
        value: "• Be clear and detailed\n• Provide screenshots if needed\n• Be patient and respectful\n• Do not create multiple tickets",
        inline: false
      }
    ],
    footer: {
      text: "Support System • Powered by Cloudflare Workers"
    },
    timestamp: new Date().toISOString()
  };
  
  const components = [{
    type: 1,
    components: [{
      type: 2,
      style: 1,
      custom_id: "open_ticket_modal",
      label: "Create Ticket",
      emoji: { name: "🎫" }
    }]
  }];
  
  // Send the panel
  await discordRequest(`/channels/${channelId}/messages`, 'POST', {
    embeds: [embed],
    components: components
  });
  
  return {
    type: 4,
    data: {
      content: "✅ Ticket panel deployed successfully!",
      flags: 64
    }
  };
}

// Handle /settings command - Configure the bot
async function handleSettings(interaction, env) {
  const embed = {
    title: "⚙️ Ticket System Settings",
    description: "Configure your ticket system using the buttons below",
    color: 0x5865f2,
    fields: [
      {
        name: "📁 Ticket Category",
        value: "Set the category where tickets will be created",
        inline: false
      },
      {
        name: "👥 Staff Role",
        value: "Set the staff role that can view tickets",
        inline: false
      },
      {
        name: "👑 Admin Role",
        value: "Set the admin role that can manage settings",
        inline: false
      },
      {
        name: "📄 Transcript Channel",
        value: "Set where closed ticket transcripts go",
        inline: false
      }
    ]
  };
  
  const components = [{
    type: 1,
    components: [
      {
        type: 2,
        style: 2,
        custom_id: "set_category",
        label: "Set Category",
        emoji: { name: "📁" }
      },
      {
        type: 2,
        style: 2,
        custom_id: "set_staff_role",
        label: "Set Staff Role",
        emoji: { name: "👥" }
      },
      {
        type: 2,
        style: 2,
        custom_id: "set_admin_role",
        label: "Set Admin Role",
        emoji: { name: "👑" }
      },
      {
        type: 2,
        style: 2,
        custom_id: "set_transcript_channel",
        label: "Set Transcript Channel",
        emoji: { name: "📄" }
      }
    ]
  }];
  
  return {
    type: 4,
    data: {
      embeds: [embed],
      components: components,
      flags: 64
    }
  };
}

// Handle ticket creation modal
async function handleTicketModal(interaction, env) {
  const guildId = interaction.guild_id;
  const userId = interaction.member.user.id;
  const userName = interaction.member.user.username;
  const subject = interaction.data.components[0].components[0].value;
  const description = interaction.data.components[1].components[0].value;
  
  const config = await getGuildConfig(guildId, env);
  
  if (!config.ticket_category_id) {
    return {
      type: 4,
      data: {
        content: "❌ Ticket system not configured! Please ask an admin to run `/settings`",
        flags: 64
      }
    };
  }
  
  // Create ticket channel
  const channelName = `ticket-${userName}-${Date.now()}`.slice(0, 32);
  
  // Setup permissions
  const permissionOverwrites = [
    {
      id: guildId,
      type: 0,
      deny: 1024 // VIEW_CHANNEL
    },
    {
      id: userId,
      type: 1,
      allow: 1024 // VIEW_CHANNEL
    }
  ];
  
  // Add staff role permission if set
  if (config.staff_role_id) {
    permissionOverwrites.push({
      id: config.staff_role_id,
      type: 0,
      allow: 1024 // VIEW_CHANNEL
    });
  }
  
  // Add admin role permission if set
  if (config.admin_role_id) {
    permissionOverwrites.push({
      id: config.admin_role_id,
      type: 0,
      allow: 1024 // VIEW_CHANNEL
    });
  }
  
  // Create the channel
  const channel = await discordRequest(`/guilds/${guildId}/channels`, 'POST', {
    name: channelName,
    type: 0,
    parent_id: config.ticket_category_id,
    permission_overwrites: permissionOverwrites,
    topic: `Ticket from ${userName}: ${subject} | Created: ${new Date().toLocaleString()}`
  });
  
  if (!channel || channel.id === undefined) {
    return {
      type: 4,
      data: {
        content: "❌ Failed to create ticket channel. Please check my permissions!",
        flags: 64
      }
    };
  }
  
  // Save ticket info to KV
  const ticketInfo = {
    user_id: userId,
    user_name: userName,
    subject: subject,
    description: description,
    created_at: Date.now(),
    channel_id: channel.id
  };
  await env.TICKET_KV.put(`ticket:${channel.id}`, JSON.stringify(ticketInfo));
  
  // Send welcome message in ticket channel
  const welcomeEmbed = {
    title: "🎫 Ticket Created",
    description: `**Subject:** ${subject}\n\n**Description:**\n${description}`,
    color: 0x00ff00,
    fields: [
      {
        name: "👤 Created By",
        value: `<@${userId}>`,
        inline: true
      },
      {
        name: "📅 Created",
        value: `<t:${Math.floor(Date.now() / 1000)}:R>`,
        inline: true
      }
    ],
    footer: {
      text: "Staff will assist you shortly"
    }
  };
  
  const components = [{
    type: 1,
    components: [
      {
        type: 2,
        style: 1,
        custom_id: "claim_ticket",
        label: "Claim Ticket",
        emoji: { name: "📋" }
      },
      {
        type: 2,
        style: 4,
        custom_id: "close_ticket",
        label: "Close Ticket",
        emoji: { name: "🔒" }
      }
    ]
  }];
  
  const mentionText = `<@${userId}>`;
  const staffMention = config.staff_role_id ? `<@&${config.staff_role_id}>` : '';
  
  await discordRequest(`/channels/${channel.id}/messages`, 'POST', {
    content: `${mentionText} ${staffMention}`,
    embeds: [welcomeEmbed],
    components: components
  });
  
  return {
    type: 4,
    data: {
      content: `✅ Ticket created! <#${channel.id}>`,
      flags: 64
    }
  };
}

// Handle claim button
async function handleClaim(interaction, env) {
  const channelId = interaction.channel_id;
  const userId = interaction.member.user.id;
  const userName = interaction.member.user.username;
  
  // Get ticket info
  const ticketInfo = await env.TICKET_KV.get(`ticket:${channelId}`, 'json');
  
  if (!ticketInfo) {
    return {
      type: 4,
      data: {
        content: "❌ Ticket not found!",
        flags: 64
      }
    };
  }
  
  // Update channel name
  await discordRequest(`/channels/${channelId}`, 'PATCH', {
    name: `claimed-${userName}`
  });
  
  // Update ticket info
  ticketInfo.claimed_by = userId;
  ticketInfo.claimed_at = Date.now();
  await env.TICKET_KV.put(`ticket:${channelId}`, JSON.stringify(ticketInfo));
  
  // Send claim message
  await discordRequest(`/channels/${channelId}/messages`, 'POST', {
    content: `📋 **Ticket claimed by** <@${userId}>`
  });
  
  return {
    type: 4,
    data: {
      content: "✅ You have claimed this ticket!",
      flags: 64
    }
  };
}

// Handle close button
async function handleClose(interaction, env) {
  const channelId = interaction.channel_id;
  const userId = interaction.member.user.id;
  const userName = interaction.member.user.username;
  
  // Get ticket info
  const ticketInfo = await env.TICKET_KV.get(`ticket:${channelId}`, 'json');
  
  if (!ticketInfo) {
    return {
      type: 4,
      data: {
        content: "❌ Ticket not found!",
        flags: 64
      }
    };
  }
  
  // Get messages for transcript
  const messages = await discordRequest(`/channels/${channelId}/messages?limit=100`);
  
  // Create transcript
  let transcript = `# Ticket Transcript\n\n`;
  transcript += `**Channel:** ${channelId}\n`;
  transcript += `**Created by:** ${ticketInfo.user_name} (${ticketInfo.user_id})\n`;
  transcript += `**Subject:** ${ticketInfo.subject}\n`;
  transcript += `**Description:** ${ticketInfo.description}\n`;
  transcript += `**Closed by:** ${userName} (${userId})\n`;
  transcript += `**Date:** ${new Date().toLocaleString()}\n\n`;
  transcript += `## Messages\n\n`;
  
  if (messages && Array.isArray(messages)) {
    for (const msg of messages.reverse()) {
      const timestamp = new Date(msg.timestamp).toLocaleString();
      transcript += `**[${timestamp}] ${msg.author.username}:** ${msg.content}\n`;
    }
  }
  
  // Save transcript to KV
  await env.TICKET_KV.put(`transcript:${channelId}`, transcript, {
    expirationTtl: 86400 * 30 // 30 days
  });
  
  // Get config for transcript channel
  const config = await getGuildConfig(interaction.guild_id, env);
  
  // Send transcript to configured channel
  if (config.transcript_channel_id) {
    // Split transcript if too long (Discord limit 2000 chars)
    const transcriptChunks = transcript.match(/.{1,1900}/g) || [];
    for (const chunk of transcriptChunks) {
      await discordRequest(`/channels/${config.transcript_channel_id}/messages`, 'POST', {
        content: `**Ticket Transcript: ${channelId}**\n\`\`\`\n${chunk}\n\`\`\``
      });
    }
  }
  
  // Delete the channel
  await discordRequest(`/channels/${channelId}`, 'DELETE');
  
  // Delete ticket from KV
  await env.TICKET_KV.delete(`ticket:${channelId}`);
  
  return {
    type: 4,
    data: {
      content: "✅ Ticket closed! Transcript saved.",
      flags: 64
    }
  };
}

// Handle setting category button
async function handleSetCategory(interaction, env) {
  // Send followup asking for category ID
  await discordRequest(`/interactions/${interaction.id}/${interaction.token}/callback`, 'POST', {
    type: 4,
    data: {
      content: "📁 Please enter the **Category ID** where tickets should be created.\n\n*Tip: Enable Developer Mode in Discord, right-click the category, and select 'Copy ID'*",
      flags: 64
    }
  });
  
  // Create a followup collector (simplified - in production you'd use a modal)
  // For now, we'll just tell them to use the edit command
  return null;
}

// Handle setting staff role button
async function handleSetStaffRole(interaction, env) {
  await discordRequest(`/interactions/${interaction.id}/${interaction.token}/callback`, 'POST', {
    type: 4,
    data: {
      content: "👥 Please enter the **Role ID** for staff members.\n\n*Tip: Enable Developer Mode in Discord, right-click the role, and select 'Copy ID'*",
      flags: 64
    }
  });
  return null;
}

// Handle setting admin role button
async function handleSetAdminRole(interaction, env) {
  await discordRequest(`/interactions/${interaction.id}/${interaction.token}/callback`, 'POST', {
    type: 4,
    data: {
      content: "👑 Please enter the **Role ID** for administrators.\n\n*Tip: Enable Developer Mode in Discord, right-click the role, and select 'Copy ID'*",
      flags: 64
    }
  });
  return null;
}

// Handle setting transcript channel button
async function handleSetTranscriptChannel(interaction, env) {
  await discordRequest(`/interactions/${interaction.id}/${interaction.token}/callback`, 'POST', {
    type: 4,
    data: {
      content: "📄 Please enter the **Channel ID** where transcripts should be sent.\n\n*Tip: Enable Developer Mode in Discord, right-click the channel, and select 'Copy ID'*",
      flags: 64
    }
  });
  return null;
}

// Handle setting responses (for modal or message input)
async function handleSettingResponse(interaction, env, settingType, value) {
  const guildId = interaction.guild_id;
  const config = await getGuildConfig(guildId, env);
  
  switch(settingType) {
    case 'category':
      config.ticket_category_id = value;
      break;
    case 'staff_role':
      config.staff_role_id = value;
      break;
    case 'admin_role':
      config.admin_role_id = value;
      break;
    case 'transcript_channel':
      config.transcript_channel_id = value;
      break;
  }
  
  await saveGuildConfig(guildId, config, env);
  
  return {
    type: 4,
    data: {
      content: `✅ ${settingType.replace('_', ' ')} set successfully!`,
      flags: 64
    }
  };
}

// Main worker handler
export default {
  async fetch(request, env, ctx) {
    // Handle Discord interactions
    if (request.method === 'POST') {
      const body = await request.json();
      
      // Verify signature (optional but recommended)
      // const signature = request.headers.get('X-Signature-Ed25519');
      // const timestamp = request.headers.get('X-Signature-Timestamp');
      // You should verify these in production
      
      // Handle different interaction types
      if (body.type === 1) { // PING
        return new Response(JSON.stringify({ type: 1 }), {
          headers: { 'Content-Type': 'application/json' }
        });
      }
      
      if (body.type === 2) { // APPLICATION_COMMAND
        const commandName = body.data.name;
        let response = null;
        
        switch(commandName) {
          case 'setup':
            response = await handleSetup(body, env);
            break;
          case 'settings':
            response = await handleSettings(body, env);
            break;
        }
        
        if (response) {
          return new Response(JSON.stringify(response), {
            headers: { 'Content-Type': 'application/json' }
          });
        }
      }
      
      if (body.type === 3) { // MESSAGE_COMPONENT (buttons)
        const customId = body.data.custom_id;
        let response = null;
        
        switch(customId) {
          case 'open_ticket_modal':
            // Show ticket creation modal
            response = {
              type: 9,
              data: {
                custom_id: "ticket_modal",
                title: "Create a Ticket",
                components: [
                  {
                    type: 1,
                    components: [{
                      type: 4,
                      custom_id: "subject",
                      label: "Subject",
                      style: 1,
                      placeholder: "Brief description of your issue",
                      required: true,
                      min_length: 5,
                      max_length: 100
                    }]
                  },
                  {
                    type: 1,
                    components: [{
                      type: 4,
                      custom_id: "description",
                      label: "Description",
                      style: 2,
                      placeholder: "Please describe your issue in detail...",
                      required: true,
                      min_length: 10,
                      max_length: 1000
                    }]
                  }
                ]
              }
            };
            break;
          case 'claim_ticket':
            response = await handleClaim(body, env);
            break;
          case 'close_ticket':
            response = await handleClose(body, env);
            break;
          case 'set_category':
            response = await handleSetCategory(body, env);
            break;
          case 'set_staff_role':
            response = await handleSetStaffRole(body, env);
            break;
          case 'set_admin_role':
            response = await handleSetAdminRole(body, env);
            break;
          case 'set_transcript_channel':
            response = await handleSetTranscriptChannel(body, env);
            break;
        }
        
        if (response) {
          return new Response(JSON.stringify(response), {
            headers: { 'Content-Type': 'application/json' }
          });
        }
      }
      
      if (body.type === 5) { // MODAL_SUBMIT
        if (body.data.custom_id === 'ticket_modal') {
          const response = await handleTicketModal(body, env);
          return new Response(JSON.stringify(response), {
            headers: { 'Content-Type': 'application/json' }
          });
        }
      }
    }
    
    // Health check endpoint
    return new Response('Ticket Bot is running!', {
      status: 200,
      headers: { 'Content-Type': 'text/plain' }
    });
  }
};
